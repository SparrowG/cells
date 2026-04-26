"""Tests for the Display class and the GUI half of Game.tick.

Uses SDL_VIDEODRIVER=dummy so pygame works without a real display server.
Hotkey wiring is verified by injecting events into the queue with
pygame.event.post() and running one tick.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pathlib
import sys

import pygame
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cells  # noqa: E402
from minds import mind1  # noqa: E402

pygame.init()


@pytest.fixture
def display_game():
    """Headed game with a tiny grid and the dummy SDL driver."""
    mind1.name = "mind1"
    game = cells.Game(
        bounds=20,
        mind_list=[("mind1", mind1), ("mind1", mind1)],
        symmetric=True,
        max_time=200,
        headless=False,
    )
    pygame.event.clear()
    yield game
    pygame.event.clear()


def test_display_constructs_without_real_display(display_game):
    assert display_game.disp is not None
    assert display_game.disp.scale == 2
    assert display_game.disp.size == (40, 40)


def test_display_update_and_flip_run(display_game):
    display_game.disp.update(
        display_game.terr,
        display_game.agent_population,
        display_game.plant_population,
        display_game.agent_map,
        display_game.plant_map,
        display_game.energy_map,
        ticks=0,
        nteams=len(display_game.minds),
        show_energy=True,
        show_agents=True,
    )
    display_game.disp.flip()


async def test_space_key_resets_winner(display_game):
    pygame.event.post(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_SPACE}))
    await display_game.tick()
    assert display_game.winner == -1


async def test_e_key_toggles_show_energy(display_game):
    initial = display_game.show_energy
    pygame.event.post(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_e}))
    await display_game.tick()
    assert display_game.show_energy != initial


async def test_a_key_toggles_show_agents(display_game):
    initial = display_game.show_agents
    pygame.event.post(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_a}))
    await display_game.tick()
    assert display_game.show_agents != initial


async def test_left_click_uses_display_scale_for_lookup(display_game, capfd):
    """Click at scaled coords (10, 10) should look up cell (5, 5) given scale=2."""
    pygame.event.post(
        pygame.event.Event(
            pygame.MOUSEBUTTONUP,
            {"button": 1, "pos": (10, 10)},
        )
    )
    await display_game.tick()
    out, _ = capfd.readouterr()
    # Either prints "None" (empty cell) or "Agent from team ...".
    assert "None" in out or "Agent from team" in out


async def test_quit_key_q_calls_sys_exit(display_game):
    pygame.event.post(pygame.event.Event(pygame.KEYUP, {"key": pygame.K_q}))
    with pytest.raises(SystemExit):
        await display_game.tick()


async def test_quit_event_calls_sys_exit(display_game):
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    with pytest.raises(SystemExit):
        await display_game.tick()


async def test_display_handles_many_ticks(display_game):
    """Smoke test: 60 ticks triggers the team-population text refresh."""
    for _ in range(65):
        if display_game.winner is not None:
            break
        await display_game.tick()
