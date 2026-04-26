#!/usr/bin/env python
"""Round-robin tournament runner for cells.

Plays each pair of minds in mind_list against each other for a configurable
number of rounds, accumulating scores (3 for a win, 1 for each side on a
draw). Writes scores.csv sorted by score descending.
"""

import argparse
import asyncio
import configparser
import random
import sys

import numpy

from cells import Game, get_mind


async def _play_game(bounds, pair, symmetric, max_time):
    game = Game(bounds, pair, symmetric, max_time, headless=True)
    while game.winner is None:
        await game.tick()
    return game.winner


def _parse_cli(argv=None):
    parser = argparse.ArgumentParser(
        description="Cells round-robin tournament.",
    )
    parser.add_argument(
        "minds",
        nargs="*",
        help="Mind module names from minds/. Need 2+ to override tournament.cfg.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed random and numpy.random for reproducible runs.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="Number of round-robin rounds. Default: 4.",
    )
    parser.add_argument(
        "--max-time",
        type=int,
        default=5000,
        help="Tick limit per game. Default: 5000.",
    )
    parser.add_argument(
        "--output",
        default="scores.csv",
        help="Output CSV path. Default: scores.csv.",
    )
    return parser.parse_args(argv)


def _load_config(path="tournament.cfg"):
    config = configparser.RawConfigParser()
    try:
        config.read(path)
        bounds = config.getint("terrain", "bounds")
        symmetric = config.getboolean("terrain", "symmetric")
        minds_str = str(config.get("minds", "minds"))
    except Exception as e:
        print("Got error: %s" % e)
        config = configparser.RawConfigParser()
        config.add_section("minds")
        config.set("minds", "minds", "mind1,mind2")
        config.add_section("terrain")
        config.set("terrain", "bounds", "300")
        config.set("terrain", "symmetric", "true")
        with open(path, "w") as f:
            config.write(f)
        bounds = config.getint("terrain", "bounds")
        symmetric = config.getboolean("terrain", "symmetric")
        minds_str = str(config.get("minds", "minds"))
    return bounds, symmetric, [n.strip() for n in minds_str.split(",") if n.strip()]


async def main_async(argv=None):
    args = _parse_cli(argv)

    if args.seed is not None:
        random.seed(args.seed)
        numpy.random.seed(args.seed)

    bounds, symmetric, cfg_minds = _load_config()

    if len(args.minds) >= 2:
        names = args.minds
    else:
        names = cfg_minds

    mind_list = [(n, get_mind(n)) for n in names]

    scores = [0 for _ in mind_list]
    pairings = [
        [mind_list[a], mind_list[b]]
        for a in range(len(mind_list))
        for b in range(a)
    ]

    for round_idx in range(args.rounds):
        # Round-robin games within a round are independent — run them
        # concurrently so a slow async mind in one pair doesn't stall
        # the others. With sync minds this is effectively sequential.
        winners = await asyncio.gather(
            *[
                _play_game(bounds, pair, symmetric, args.max_time)
                for pair in pairings
            ]
        )
        for pair, winner in zip(pairings, winners):
            if winner >= 0:
                scores[mind_list.index(pair[winner])] += 3
            elif winner == -1:
                scores[mind_list.index(pair[0])] += 1
                scores[mind_list.index(pair[1])] += 1
            print(scores)
            print([m[0] for m in mind_list])

    name_score = list(zip([m[0] for m in mind_list], scores))
    name_score.sort(key=lambda ns: -ns[1])
    with open(args.output, "w") as f:
        for name, score in name_score:
            f.write("%s;%s\n" % (name, score))


def main(argv=None):
    asyncio.run(main_async(argv))


if __name__ == "__main__":
    main()
