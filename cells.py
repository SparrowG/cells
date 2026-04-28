#!/usr/bin/env python

import asyncio
import collections
import configparser
import inspect
import random
import sys
import time

import numpy
import pygame, pygame.locals

from terrain.generator import terrain_generator

if not pygame.font:
    print('Warning, fonts disabled')


def get_mind(name):
    full_name = 'minds.' + name
    __import__(full_name)
    mind = sys.modules[full_name]
    mind.name = name
    return mind


SOFT_TIMEOUT_SECONDS = 5.0
HARD_TIMEOUT_SECONDS = 60.0


async def _call_act(act_fn, view, msg):
    """Invoke a mind's act() under a 60s hard ceiling.

    Works with both sync (`def act`) and async (`async def act`) minds:
    if the return value is awaitable, await it; otherwise return as-is.
    Cancellation propagates so the caller's wait_for can shield without
    leaking tasks past game end.
    """
    async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
        result = act_fn(view, msg)
        if inspect.isawaitable(result):
            return await result
        return result


def _enqueue_actions(agent, result):
    if result is None:
        return
    if isinstance(result, list):
        agent.action_queue.extend(a for a in result if a is not None)
    else:
        agent.action_queue.append(result)


async def _act_for_agent(agent, view, msg, *, is_disqualified=False, on_strike=None):
    """Determine this tick's action for an agent under the 5s soft / 60s hard
    timeout model. May queue future actions for subsequent ticks if the mind
    returned a list. Falls back to last_action (or ACT_EAT noop) if the mind
    is still in flight, raised, or returned None.

    If `is_disqualified` is True, the agent NOOPs unconditionally — its
    team has burned through too many strikes (see #25).

    `on_strike(reason)` is invoked once per fallback event so the caller
    can count strikes. Reasons: 'soft_timeout', 'hard_timeout',
    'exception', 'malformed'.
    """
    if is_disqualified:
        return Action(ACT_EAT)

    if agent.action_queue:
        action = agent.action_queue.popleft()
        agent.last_action = action
        return action

    fresh = None
    fresh_reason = None

    if agent.pending_task is not None and agent.pending_task.done():
        try:
            fresh = agent.pending_task.result()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            fresh_reason = "hard_timeout"
        except Exception:
            fresh_reason = "exception"
        agent.pending_task = None
        if fresh is None and fresh_reason is None:
            fresh_reason = "malformed"

    if fresh is None and agent.pending_task is None:
        agent.pending_task = asyncio.create_task(
            _call_act(agent.act, view, msg)
        )
        try:
            fresh = await asyncio.wait_for(
                asyncio.shield(agent.pending_task), SOFT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            fresh_reason = "soft_timeout"
        except asyncio.CancelledError:
            fresh_reason = "hard_timeout"
            agent.pending_task = None
        except Exception:
            fresh_reason = "exception"
            agent.pending_task = None
        else:
            agent.pending_task = None
            if fresh is None:
                fresh_reason = "malformed"

    if fresh is not None:
        _enqueue_actions(agent, fresh)
        if agent.action_queue:
            action = agent.action_queue.popleft()
            agent.last_action = action
            return action

    # If we still have nothing fresh and a task is in flight from a prior
    # tick, the bot is keeping us in fall-back territory — count a soft
    # strike for this tick. Without this, a permanently hung bot would
    # only ever accumulate one strike (on the tick the call was first
    # spawned) and never trip the DQ threshold.
    if fresh is None and fresh_reason is None and agent.pending_task is not None:
        fresh_reason = "soft_timeout"

    if fresh_reason is not None and on_strike is not None:
        on_strike(fresh_reason)

    return agent.last_action if agent.last_action is not None else Action(ACT_EAT)



STARTING_ENERGY = 20
SCATTERED_ENERGY = 10 

#Plant energy output. Remember, this should always be less
#than ATTACK_POWER, because otherwise cells sitting on the plant edge
#might become invincible.
PLANT_MAX_OUTPUT = 20
PLANT_MIN_OUTPUT = 5

#BODY_ENERGY is the amount of energy that a cells body contains
#It can not be accessed by the cells, think of it as: they can't
#eat their own body. It is released again at death.
BODY_ENERGY  = 25
ATTACK_POWER = 30
#Amount by which attack power is modified for each 1 height difference.
ATTACK_TERR_CHANGE = 2
ENERGY_CAP   = 2500

#SPAWN_COST is the energy it takes to seperate two cells from each other.
#It is lost forever, not to be confused with the BODY_ENERGY of the new cell.
SPAWN_LOST_ENERGY = 20
SUSTAIN_COST      = 0
MOVE_COST         = 1    
#MESSAGE_COST    = 0    

#BODY_ENERGY + SPAWN_COST is invested to create a new cell. What remains is split evenly.
#With this model we only need to make sure a cell can't commit suicide by spawning.
SPAWN_TOTAL_ENERGY = BODY_ENERGY + SPAWN_LOST_ENERGY

TIMEOUT = None

config = configparser.RawConfigParser()


def get_next_move(old_x, old_y, x, y):
    ''' Takes the current position, old_x and old_y, and a desired future position, x and y,
    and returns the position (x,y) resulting from a unit move toward the future position.'''
    dx = numpy.sign(x - old_x)
    dy = numpy.sign(y - old_y)
    return (old_x + dx, old_y + dy)


class Game(object):
    ''' Represents a game between different minds. '''
    def __init__(self, bounds, mind_list, symmetric, max_time, headless=False, strike_threshold=3):
        self.size = self.width, self.height = (bounds, bounds)
        self.mind_list = mind_list
        self.messages = [MessageQueue() for x in mind_list]
        self.headless = headless
        if not self.headless:
            self.disp = Display(self.size, scale=2)
        self.time = 0
        self.clock = pygame.time.Clock()
        self.max_time = max_time
        self.strike_threshold = strike_threshold
        self.strikes = [0 for _ in mind_list]
        self.disqualified = set()
        self.strike_log = []  # (tick, team, reason, count)
        self.tic = time.time()
        self.terr = ScalarMapLayer(self.size)
        self.terr.set_perlin(10, symmetric)
        self.minds = [m[1].AgentMind for m in mind_list]

        self.show_energy = True
        self.show_agents = True

        self.energy_map = ScalarMapLayer(self.size)
        self.energy_map.set_streak(SCATTERED_ENERGY, symmetric)

        self.plant_map = ObjectMapLayer(self.size)
        self.plant_population = []

        self.agent_map = ObjectMapLayer(self.size)
        self.agent_population = []
        self.winner = None
        if symmetric:
            self.n_plants = 7
        else:
            self.n_plants = 14
            
        # Add some randomly placed plants to the map.
        for x in range(self.n_plants):
            mx = random.randrange(1, self.width - 1)
            my = random.randrange(1, self.height - 1)
            eff = random.randrange(PLANT_MIN_OUTPUT, PLANT_MAX_OUTPUT)
            p = Plant(mx, my, eff)
            self.plant_population.append(p)
            if symmetric:
                p = Plant(my, mx, eff)
                self.plant_population.append(p)
        self.plant_map.lock()
        self.plant_map.insert(self.plant_population)
        self.plant_map.unlock()

        # Create an agent for each mind and place on map at a different plant.
        self.agent_map.lock()
        for idx in range(len(self.minds)):
            # BUG: Number of minds could exceed number of plants?
            (mx, my) = self.plant_population[idx].get_pos()
            fuzzed_x = mx
            fuzzed_y = my
            while fuzzed_x == mx and fuzzed_y == my:
                fuzzed_x = mx + random.randrange(-1, 2)
                fuzzed_y = my + random.randrange(-1, 2)
            self.agent_population.append(Agent(fuzzed_x, fuzzed_y, STARTING_ENERGY, idx,
                                               self.minds[idx], None))
            self.agent_map.insert(self.agent_population)
        self.agent_map.unlock()

    def run_plants(self):
        ''' Increases energy at and around (adjacent position) for each plant.
        Increase in energy is equal to the eff(?) value of each the plant.'''
        for p in self.plant_population:
            (x, y) = p.get_pos()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    adj_x = x + dx
                    adj_y = y + dy
                    if self.energy_map.in_range(adj_x, adj_y):
                        self.energy_map.change(adj_x, adj_y, p.get_eff())


    def add_agent(self, a):
        ''' Adds an agent to the game. '''
        self.agent_population.append(a)
        self.agent_map.set(a.x, a.y, a)

    def del_agent(self, a):
        ''' Kills the agent (if not already dead), removes them from the game and
        drops any load they were carrying in there previously occupied position. '''
        self.agent_population.remove(a)
        self.agent_map.set(a.x, a.y, None)
        a.alive = False
        if a.loaded:
            a.loaded = False
            self.terr.change(a.x, a.y, 1)
        if a.pending_task is not None and not a.pending_task.done():
            a.pending_task.cancel()
        a.pending_task = None

    async def _cancel_all_pending(self):
        pending = [
            a.pending_task for a in self.agent_population
            if a.pending_task is not None and not a.pending_task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for a in self.agent_population:
            a.pending_task = None

    def _record_strike(self, team, reason):
        self.strikes[team] += 1
        self.strike_log.append((self.time, team, reason, self.strikes[team]))
        if self.strikes[team] >= self.strike_threshold and team not in self.disqualified:
            self.disqualified.add(team)
            self.strike_log.append((self.time, team, "DISQUALIFIED", self.strikes[team]))

    def move_agent(self, a, x, y):
        ''' Moves agent, a, to new position (x,y) unless difference in terrain levels between
        its current position and new position is greater than 4.'''
        if abs(self.terr.get(x, y)-self.terr.get(a.x, a.y)) <= 4:
            self.agent_map.set(a.x, a.y, None)
            self.agent_map.set(x, y, a)
            a.x = x
            a.y = y

    async def _dispatch_team(self, team, agent_views):
        """Resolve this tick's action for every agent on `team`. Uses the
        per-team batch protocol (#23) if the team's mind module exposes
        `act_batch`; otherwise falls back to the per-agent path that
        carries the #18 soft/hard timeout model."""
        is_dq = team in self.disqualified
        mind_module = self.mind_list[team][1]
        if not is_dq and hasattr(mind_module, "act_batch"):
            return await self._dispatch_team_batch(team, mind_module, agent_views)
        return await asyncio.gather(*[
            _act_for_agent(
                a, v, self.messages[team],
                is_disqualified=is_dq,
                on_strike=(lambda reason, t=team: self._record_strike(t, reason)),
            )
            for (a, v) in agent_views
        ])

    async def _dispatch_team_batch(self, team, mind_module, agent_views):
        """One batch call per team per tick. Agents with pre-planned moves
        from a prior tick consume their queue first; only the rest are
        sent. Missing entries in the response strike the team and fall
        back to last_action — matching the per-agent semantics."""
        results = [None] * len(agent_views)
        for i, (a, _) in enumerate(agent_views):
            if a.action_queue:
                action = a.action_queue.popleft()
                a.last_action = action
                results[i] = action

        pending_idx = [i for i in range(len(agent_views)) if results[i] is None]
        if not pending_idx:
            return results

        batch_input = [(agent_views[i][0].id, agent_views[i][1]) for i in pending_idx]
        msg = self.messages[team]

        raw = None
        reason = None
        try:
            raw = await asyncio.wait_for(
                mind_module.act_batch(batch_input, msg),
                SOFT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            reason = "soft_timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            reason = "exception"

        if not isinstance(raw, dict):
            raw = {}

        for i in pending_idx:
            a = agent_views[i][0]
            agent_result = raw.get(a.id)
            if agent_result is None:
                self._record_strike(team, reason or "malformed")
                fallback = a.last_action if a.last_action is not None else Action(ACT_EAT)
                results[i] = fallback
            elif isinstance(agent_result, list):
                if not agent_result:
                    self._record_strike(team, "malformed")
                    fallback = a.last_action if a.last_action is not None else Action(ACT_EAT)
                    results[i] = fallback
                else:
                    a.action_queue.extend(agent_result[1:])
                    a.last_action = agent_result[0]
                    results[i] = agent_result[0]
            else:
                a.last_action = agent_result
                results[i] = agent_result
        return results

    async def run_agents(self):
        # Create a list containing the view for each agent in the population.
        views = []
        agent_map_get_small_view_fast = self.agent_map.get_small_view_fast
        plant_map_get_small_view_fast = self.plant_map.get_small_view_fast
        energy_map = self.energy_map
        terr_map = self.terr
        WV = WorldView
        views_append = views.append
        for a in self.agent_population:
            x = a.x
            y = a.y
            agent_view = agent_map_get_small_view_fast(x, y)
            plant_view = plant_map_get_small_view_fast(x, y)
            world_view = WV(a, agent_view, plant_view, terr_map, energy_map, self.time)
            views_append((a, world_view))

        # Group views by team and dispatch one team at a time (#23). If the
        # team's mind exposes act_batch, the team's agents share a single
        # call per tick; otherwise the engine falls back to per-agent
        # dispatch (preserving the soft/hard timeout model from #18).
        by_team = collections.defaultdict(list)
        for (a, v) in views:
            by_team[a.team].append((a, v))
        team_ids = sorted(by_team.keys())
        per_team_results = await asyncio.gather(
            *[self._dispatch_team(t, by_team[t]) for t in team_ids]
        )
        acts_by_agent = {}
        for tid, results in zip(team_ids, per_team_results):
            for (a, _), action in zip(by_team[tid], results):
                acts_by_agent[a] = action
        agents = [a for (a, _) in views]
        acts = [acts_by_agent[a] for a in agents]
        actions = list(zip(agents, acts))
        actions_dict = dict(actions)
        random.shuffle(actions)

        self.agent_map.lock()
        # Apply the action for each agent - in doing so agent uses up 1 energy unit.
        for (agent, action) in actions:
            #This is the cost of mere survival
            agent.energy -= SUSTAIN_COST

            if action.type == ACT_MOVE: # Changes position of agent.
                act_x, act_y = action.get_data()
                (new_x, new_y) = get_next_move(agent.x, agent.y,
                                               act_x, act_y)
                # Move to the new position if it is in range and it's not 
                #currently occupied by another agent.
                if (self.agent_map.in_range(new_x, new_y) and
                    not self.agent_map.get(new_x, new_y)):
                    self.move_agent(agent, new_x, new_y)
                    agent.energy -= MOVE_COST
            elif action.type == ACT_SPAWN: # Creates new agents and uses additional 50 energy units.
                act_x, act_y = action.get_data()[:2]
                (new_x, new_y) = get_next_move(agent.x, agent.y,
                                               act_x, act_y)
                if (self.agent_map.in_range(new_x, new_y) and
                    not self.agent_map.get(new_x, new_y) and
                    agent.energy >= SPAWN_TOTAL_ENERGY):
                    agent.energy -= SPAWN_TOTAL_ENERGY
                    agent.energy /= 2
                    a = Agent(new_x, new_y, agent.energy, agent.get_team(),
                              self.minds[agent.get_team()],
                              action.get_data()[2:])
                    self.add_agent(a)
            elif action.type == ACT_EAT:
                #Eat only as much as possible.
                intake = min(self.energy_map.get(agent.x, agent.y),
                            ENERGY_CAP - agent.energy)
                agent.energy += intake
                self.energy_map.change(agent.x, agent.y, -intake)
            elif action.type == ACT_RELEASE:
                #Dump some energy onto an adjacent field
                #No Seppuku
                output = action.get_data()[2]
                output = min(agent.energy - 1, output) 
                act_x, act_y = action.get_data()[:2]
                #Use get_next_move to simplyfy things if you know 
                #where the energy is supposed to end up.
                (out_x, out_y) = get_next_move(agent.x, agent.y,
                                               act_x, act_y)
                if (self.agent_map.in_range(out_x, out_y) and
                    agent.energy >= 1):
                    agent.energy -= output
                    self.energy_map.change(out_x, out_y, output)
            elif action.type == ACT_ATTACK:
                #Make sure agent is attacking an adjacent field.
                act_x, act_y = act_data = action.get_data()
                next_pos = get_next_move(agent.x, agent.y, act_x, act_y)
                new_x, new_y = next_pos
                victim = self.agent_map.get(act_x, act_y)
                terr_delta = (self.terr.get(agent.x, agent.y) 
                            - self.terr.get(act_x, act_y))
                if (victim is not None and victim.alive and
                    next_pos == act_data):
                    #If both agents attack each other, both loose double energy
                    #Think twice before attacking 
                    try:
                        contested = (actions_dict[victim].type == ACT_ATTACK)
                    except:
                        contested = False
                    agent.attack(victim, terr_delta, contested)
                    if contested:
                        victim.attack(agent, -terr_delta, True)
                     
            elif action.type == ACT_LIFT:
                if not agent.loaded and self.terr.get(agent.x, agent.y) > 0:
                    agent.loaded = True
                    self.terr.change(agent.x, agent.y, -1)
                    
            elif action.type == ACT_DROP:
                if agent.loaded:
                    agent.loaded = False
                    self.terr.change(agent.x, agent.y, 1)

        # Kill all agents with negative energy.
        team = [0 for n in self.minds]
        for (agent, action) in actions:
            if agent.energy < 0 and agent.alive:
                self.energy_map.change(agent.x, agent.y, BODY_ENERGY)
                self.del_agent(agent)
            else :
                team[agent.team] += 1
            
        # Team wins (and game ends) if opposition team has 0 agents remaining.
        # Draw if time exceeds time limit.
        winner = 0
        alive = 0
        for t in team:
            if t != 0:
                alive += 1
            else:
                if alive == 0:
                    winner += 1
        
        if alive == 1:
            colors = ["red", "white", "purple", "yellow"]
            print("Winner is %s (%s) in %s" % (self.mind_list[winner][1].name,
                                                colors[winner], str(self.time)))
            self.winner = winner
        
        if alive == 0 or (self.max_time > 0 and self.time > self.max_time):
            print("It's a draw!")
            self.winner = -1

        self.agent_map.unlock()

        if self.winner is not None:
            await self._cancel_all_pending()

    async def tick(self):
        if not self.headless:
            scale = self.disp.scale
            for event in pygame.event.get():
                if event.type == pygame.locals.KEYUP:
                    if event.key == pygame.locals.K_SPACE:
                        self.winner = -1
                    elif event.key == pygame.locals.K_q:
                        sys.exit()
                    elif event.key == pygame.locals.K_e:
                        self.show_energy = not self.show_energy
                    elif event.key == pygame.locals.K_a:
                        self.show_agents = not self.show_agents
                elif event.type == pygame.locals.MOUSEBUTTONUP:
                    if event.button == 1:
                        print(self.agent_map.get(event.pos[0] // scale,
                                                 event.pos[1] // scale))
                elif event.type == pygame.QUIT:
                    sys.exit()
            self.disp.update(self.terr, self.agent_population,
                             self.plant_population, self.agent_map,
                             self.plant_map, self.energy_map, self.time,
                             len(self.minds), self.show_energy,
                             self.show_agents)
            pygame.event.pump()
            self.disp.flip()

        await self.run_agents()
        self.run_plants()
        for msg in self.messages:
            msg.update()
        self.time += 1
        self.tic = time.time()
        self.clock.tick()
        if self.time % 100 == 0:
            print('FPS: %f' % self.clock.get_fps())


class MapLayer(object):
    def __init__(self, size, val=0, valtype=numpy.object_):
        self.size = self.width, self.height = size
        self.values = numpy.empty(size, valtype)
        self.values.fill(val)

    def get(self, x, y):
        if y >= 0 and x >= 0:
            try:
                return self.values[x, y]
            except IndexError:
                return None
        return None

    def set(self, x, y, val):
        self.values[x, y] = val

    def in_range(self, x, y):
        return (0 <= x < self.width and 0 <= y < self.height)


class ScalarMapLayer(MapLayer):
    def set_random(self, range, symmetric = True):
        self.values = terrain_generator().create_random(self.size, range, 
                                                        symmetric)

    def set_streak(self, range, symmetric = True):
        self.values = terrain_generator().create_streak(self.size, range,
                                                        symmetric)

    def set_simple(self, range, symmetric = True):
        self.values = terrain_generator().create_simple(self.size, range,
                                                        symmetric)
    
    def set_perlin(self, range, symmetric = True):
        self.values = terrain_generator().create_perlin(self.size, range,
                                                        symmetric)


    def change(self, x, y, val):
        self.values[x, y] += val


class ObjectMapLayer(MapLayer):
    def __init__(self, size):
        MapLayer.__init__(self, size, None, numpy.object_)
        self.surf = pygame.Surface(size)
        self.surf.set_colorkey((0,0,0))
        self.surf.fill((0,0,0))
        self.pixels = None

    def lock(self):
        self.pixels = pygame.surfarray.pixels2d(self.surf)

    def unlock(self):
        self.pixels = None

    def get_small_view_fast(self, x, y):
        ret = []
        get = self.get
        append = ret.append
        width = self.width
        height = self.height
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                try:
                    adj_x = x + dx
                    if not 0 <= adj_x < width:
                        continue
                    adj_y = y + dy
                    if not 0 <= adj_y < height:
                        continue
                    a = self.values[adj_x, adj_y]
                    if a is not None:
                        append(a.get_view())
                except IndexError:
                    pass
        return ret

    def get_view(self, x, y, r):
        ret = []
        for x_off in range(-r, r + 1):
            for y_off in range(-r, r + 1):
                if x_off == 0 and y_off == 0:
                    continue
                a = self.get(x + x_off, y + y_off)
                if a is not None:
                    ret.append(a.get_view())
        return ret

    def insert(self, list):
        for o in list:
            self.set(o.x, o.y, o)

    def set(self, x, y, val):
        MapLayer.set(self, x, y, val)
        if val is None:
            self.pixels[x][y] = 0
#            self.surf.set_at((x, y), 0)
        else:
            self.pixels[x][y] = val.color
#            self.surf.set_at((x, y), val.color)


# Use Cython version of get_small_view_fast if available.
# Otherwise, don't bother folks about it.
try:
    import cells_helpers
    ObjectMapLayer.get_small_view_fast = cells_helpers.get_small_view_fast
except ImportError:
    pass

TEAM_COLORS = [(255, 0, 0), (255, 255, 255), (255, 0, 255), (255, 255, 0)]
TEAM_COLORS_FAST = [0xFF0000, 0xFFFFFF, 0xFF00FF, 0xFFFF00]

class Agent(object):
    __slots__ = ['x', 'y', 'mind', 'energy', 'alive', 'team', 'loaded', 'color',
                 'act', 'action_queue', 'last_action', 'pending_task', 'id']
    _next_id = 0
    def __init__(self, x, y, energy, team, AgentMind, cargs):
        self.x = x
        self.y = y
        self.mind = AgentMind(cargs)
        self.energy = energy
        self.alive = True
        self.team = team
        self.loaded = False
        self.color = TEAM_COLORS_FAST[team % len(TEAM_COLORS_FAST)]
        self.act = self.mind.act
        self.action_queue = collections.deque()
        self.last_action = None
        self.pending_task = None
        Agent._next_id += 1
        self.id = "agent-%d" % Agent._next_id
    def __str__(self):
        return "Agent from team %i, energy %i" % (self.team,self.energy)
    def attack(self, other, offset = 0, contested = False):
        if not other:
            return False
        max_power = ATTACK_POWER + ATTACK_TERR_CHANGE * offset
        if contested:
            other.energy -= min(self.energy, max_power)
        else:
            other.energy -= max_power
        return other.energy <= 0

    def get_team(self):
        return self.team

    def get_pos(self):
        return (self.x, self.y)

    def set_pos(self, x, y):
        self.x = x
        self.y = y

    def get_view(self):
        return AgentView(self)

# Actions available to an agent on each turn.
ACT_SPAWN, ACT_MOVE, ACT_EAT, ACT_RELEASE, ACT_ATTACK, ACT_LIFT, ACT_DROP = range(7)

class Action(object):
    '''
    A class for passing an action around.
    '''
    def __init__(self, action_type, data=None):
        self.type = action_type
        self.data = data

    def get_data(self):
        return self.data

    def get_type(self):
        return self.type


class PlantView(object):
    def __init__(self, p):
        self.x = p.x
        self.y = p.y
        self.eff = p.get_eff()

    def get_pos(self):
        return (self.x, self.y)

    def get_eff(self):
        return self.eff


class AgentView(object):
    def __init__(self, agent):
        (self.x, self.y) = agent.get_pos()
        self.team = agent.get_team()

    def get_pos(self):
        return (self.x, self.y)

    def get_team(self):
        return self.team


class WorldView(object):
    def __init__(self, me, agent_views, plant_views, terr_map, energy_map, tick=0):
        self.agent_views = agent_views
        self.plant_views = plant_views
        self.energy_map = energy_map
        self.terr_map = terr_map
        self.me = me
        self.tick = tick

    def get_me(self):
        return self.me

    def get_agents(self):
        return self.agent_views

    def get_plants(self):
        return self.plant_views

    def get_terr(self):
        return self.terr_map

    def get_energy(self):
        return self.energy_map

    def to_json(self):
        """Serializable snapshot of this view, suitable for sending over a
        network transport (HTTP, MCP). Terrain and energy are 3x3 patches
        centered on the agent; out-of-range cells are null."""

        def _scalar(v):
            return None if v is None else int(v)

        me = self.me
        cx, cy = me.get_pos()
        return {
            "me": {
                "team": int(me.get_team()),
                "energy": int(me.energy),
                "loaded": bool(me.loaded),
                "pos": [int(cx), int(cy)],
            },
            "agents": [
                {"team": int(a.get_team()), "pos": [int(a.get_pos()[0]), int(a.get_pos()[1])]}
                for a in self.agent_views
            ],
            "plants": [
                {"eff": int(p.get_eff()), "pos": [int(p.get_pos()[0]), int(p.get_pos()[1])]}
                for p in self.plant_views
            ],
            "terrain": [
                [_scalar(self.terr_map.get(cx - 1 + dx, cy - 1 + dy)) for dy in range(3)]
                for dx in range(3)
            ],
            "energy": [
                [_scalar(self.energy_map.get(cx - 1 + dx, cy - 1 + dy)) for dy in range(3)]
                for dx in range(3)
            ],
            "tick": int(self.tick),
        }


class Display(object):
    black = (0, 0, 0)
    red = (255, 0, 0)
    green = (0, 255, 0)
    yellow = (255, 255, 0)

    def __init__(self, size, scale=2):
        self.width, self.height = size
        self.scale = scale
        self.size = (self.width * scale, self.height * scale)
        pygame.init()
        self.screen  = pygame.display.set_mode(self.size)
        self.surface = self.screen
        pygame.display.set_caption("Cells")

        self.background = pygame.Surface(self.screen.get_size())
        self.background = self.background.convert()
        self.background.fill((150,150,150))

        self.text = []

    if pygame.font:
        def show_text(self, text, color, topleft):
            font = pygame.font.Font(None, 24)
            text = font.render(text, 1, color)
            textpos = text.get_rect()
            textpos.topleft = topleft
            self.text.append((text, textpos))
    else:
        def show_text(self, text, color, topleft):
            pass

    def update(self, terr, pop, plants, agent_map, plant_map, energy_map,
               ticks, nteams, show_energy, show_agents):
        r = numpy.minimum(150, 20 * terr.values)
        r <<= 16

        img = r
        if show_energy:
            g = terr.values + energy_map.values
            g *= 10
            g = numpy.minimum(150, g)
            g <<= 8
            img += g

        img_surf = pygame.Surface((self.width, self.height))
        pygame.surfarray.blit_array(img_surf, img)
        if show_agents:
            img_surf.blit(agent_map.surf, (0,0))
        img_surf.blit(plant_map.surf, (0,0))

        scale = self.scale
        pygame.transform.scale(img_surf,
                               self.size, self.screen)
        if not ticks % 60:
            #todo: find out how many teams are playing
            team_pop = [0] * nteams

            for team in range(nteams):
                team_pop[team] = sum(1 for a in pop if a.team == team)

            self.text = []
            drawTop = 0
            for t in range(nteams):
                drawTop += 20
                self.show_text(str(team_pop[t]), TEAM_COLORS[t], (10, drawTop))

        for text, textpos in self.text:
            self.surface.blit(text, textpos)

    def flip(self):
        pygame.display.flip()


class Plant(object):
    color = 0x00FF00
 
    def __init__(self, x, y, eff):
        self.x = x
        self.y = y
        self.eff = eff

    def get_pos(self):
        return (self.x, self.y)

    def get_eff(self):
        return self.eff

    def get_view(self):
        return PlantView(self)


class MessageQueue(object):
    def __init__(self):
        self.__inlist = []
        self.__outlist = []

    def update(self):
        self.__outlist = self.__inlist
        self.__inlist = []

    def send_message(self, m):
        self.__inlist.append(m)

    def get_messages(self):
        return self.__outlist


class Message(object):
    def __init__(self, message):
        self.message = message
    def get_message(self):
        return self.message


def _parse_cli(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Cells: a multi-agent Python programming game.",
    )
    parser.add_argument(
        "minds",
        nargs="*",
        help=(
            "With --bots: bot names from bots.toml to play against each other. "
            "Without --bots: legacy mind module names from minds/ — deprecated."
        ),
    )
    parser.add_argument(
        "--bots",
        default=None,
        help="Path to bots.toml. The canonical source of mind config (#24).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without opening a display window. Exits after one game.",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=None,
        help="Tick limit. Default: -1 (no limit) with display, 5000 headless.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed random and numpy.random for reproducible runs.",
    )
    args = parser.parse_args(argv)
    if args.max_time is None:
        args.max_time = 5000 if args.headless else -1
    return args


def main(argv=None):
    args = _parse_cli(argv)

    if args.seed is not None:
        random.seed(args.seed)
        numpy.random.seed(args.seed)

    if args.bots:
        from config import load_bots, select_bots

        mind_list, tournament_cfg = load_bots(args.bots)
        if args.minds:
            mind_list = select_bots(mind_list, args.minds)
        bounds = int(tournament_cfg.get("bounds", 300))
        symmetric = bool(tournament_cfg.get("symmetric", True))
        return args, bounds, symmetric, mind_list

    from config import warn_legacy_cfg

    warn_legacy_cfg("default.cfg")

    try:
        config.read('default.cfg')
        bounds = config.getint('terrain', 'bounds')
        symmetric = config.getboolean('terrain', 'symmetric')
        minds_str = str(config.get('minds', 'minds'))
    except Exception as e:
        print('Got error: %s' % e)
        config.add_section('minds')
        config.set('minds', 'minds', 'mind1,mind2')
        config.add_section('terrain')
        config.set('terrain', 'bounds', '300')
        config.set('terrain', 'symmetric', 'true')

        with open('default.cfg', 'w') as configfile:
            config.write(configfile)

        config.read('default.cfg')
        bounds = config.getint('terrain', 'bounds')
        symmetric = config.getboolean('terrain', 'symmetric')
        minds_str = str(config.get('minds', 'minds'))

    if len(args.minds) >= 2:
        mind_list = [(n, get_mind(n)) for n in args.minds]
    else:
        mind_list = [(n, get_mind(n)) for n in minds_str.split(',')]

    return args, bounds, symmetric, mind_list


async def _run_loop(args, bounds, symmetric, mind_list):
    while True:
        game = Game(bounds, mind_list, symmetric, args.max_time, headless=args.headless)
        while game.winner is None:
            await game.tick()
        if args.headless:
            break


if __name__ == "__main__":
    args, bounds, symmetric, mind_list = main()
    asyncio.run(_run_loop(args, bounds, symmetric, mind_list))
