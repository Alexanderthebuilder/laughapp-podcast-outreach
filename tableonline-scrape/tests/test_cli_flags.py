"""--limit and --resume must work on either side of the subcommand.

They were defined only on the top-level parser, so the natural
`sweep --limit 20` was rejected with "unrecognized arguments".
"""
import importlib

import pytest

PHASES = ["phase1_enumerate", "phase2_detail", "phase3_places",
          "phase4_website_crawl", "phase5_registry", "phase6_secondary",
          "phase7_pipedrive"]

FIRST_SUBCOMMAND = {"phase1_enumerate": "sweep", "phase2_detail": "http",
                    "phase3_places": "match", "phase4_website_crawl": "crawl",
                    "phase5_registry": "match", "phase6_secondary": "patterns",
                    "phase7_pipedrive": "score"}


@pytest.mark.parametrize("name", PHASES)
def test_limit_accepted_after_the_subcommand(name):
    mod = importlib.import_module(f"src.{name}")
    cmd = FIRST_SUBCOMMAND[name]
    # --help exits 0 once parsing succeeded, so the phase never runs.
    with pytest.raises(SystemExit) as exc:
        mod.main([cmd, "--limit", "20", "--help"])
    assert exc.value.code == 0, f"{name} {cmd} rejected --limit after subcommand"


@pytest.mark.parametrize("name", PHASES)
def test_limit_still_accepted_before_the_subcommand(name):
    mod = importlib.import_module(f"src.{name}")
    with pytest.raises(SystemExit) as exc:
        mod.main(["--limit", "20", FIRST_SUBCOMMAND[name], "--help"])
    assert exc.value.code == 0


def test_subcommand_flag_does_not_clobber_a_top_level_value():
    from src._cli import base_parser, subcommands
    p = base_parser("t")
    sub = subcommands(p)
    sub.add_parser("sweep")
    assert p.parse_args(["--limit", "7", "sweep"]).limit == 7
    assert p.parse_args(["sweep", "--limit", "7"]).limit == 7
    assert p.parse_args(["sweep"]).limit is None
    assert p.parse_args(["sweep", "--resume"]).resume is True
    assert p.parse_args(["sweep"]).resume is False
