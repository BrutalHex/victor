"""Command priority table (dispatch.py): every phrase family, asleep and awake,
including the misheard transcripts from the 12 Oct 2026 logs."""
import pytest

import dispatch

AWAKE = [
    # rule 1 sleep
    ("Stop Vector.", "sleep"), ("Stop, Vector!", "sleep"), ("Vector stop", "sleep"), ("Vector, stop.", "sleep"),
    ("Hey Vector, stop", "sleep"), ("Hey Vector stop.", "sleep"), ("Hey, Vector. Stop.", "sleep"),
    ("Hey Vector, stop listening", "sleep"), ("stop listening", "sleep"), ("Stop it, Vector.", "sleep"),
    ("go to sleep", "sleep"), ("Go to sleep.", "sleep"), ("Vector, go to sleep", "sleep"),
    ("Hey Vector, go to sleep", "sleep"), ("Stopp Vektor", "sleep"), ("Geh schlafen", "sleep"),
    ("Hallo Vektor, stopp", "sleep"), ("بخواب", "sleep"), ("وکتور بسه", "sleep"), ("Stop Victor", "sleep"),
    # rule 2 halt (stays awake)
    ("stop", "halt"), ("Stop.", "halt"), ("halt", "halt"), ("stopp", "halt"), ("ایست", "halt"),
    # rule 3 wake
    ("Hey Vector", "wake"), ("Hey Vector!", "wake"), ("Vector?", "wake"), ("Hey Vector, what time is it?", "wake"),
    ("Hey Vector, who is my cat?", "wake"),
    # rules 5-10
    ("what time is it", "command"), ("turn left", "command"), ("dance", "command"),
    ("who is my dog?", "pet"), ("Who is my catty?", "pet"), ("My cat.", "pet"), ("سگم کیه؟", "pet"),
    ("dance to the music", "music"),
    ("who am I", "face"), ("my name is Ann", "face"),
    ("The news said it will rain in Tehran tomorrow and the markets fell.", "brain"),
    ("Why is the sky blue?", "brain"),
]

ASLEEP = [
    ("Stop Vector.", "sleep"), ("Hey Vector, stop", "sleep"), ("go to sleep", "sleep"), ("Vector stop", "sleep"),
    ("Hey Vector", "wake"), ("Hey Vector, who is my cat?", "wake"),
    ("who is my cat?", "asleep_gate"), ("stop", "asleep_gate"), ("dance to the music", "asleep_gate"),
    ("what time is it", "asleep_gate"), ("I was telling my friend about the weather today.", "asleep_gate"),
    ("my name is Ann", "asleep_gate"),
]


@pytest.mark.parametrize("text,rule", AWAKE)
def test_awake(text, rule):
    assert dispatch.route(text, asleep=False).rule == rule


@pytest.mark.parametrize("text,rule", ASLEEP)
def test_asleep(text, rule):
    assert dispatch.route(text, asleep=True).rule == rule


def test_wake_rest_runs_command():
    r = dispatch.route("Hey Vector, what time is it?", asleep=True)
    assert r.rule == "wake" and dispatch.classify_awake(r.text).rule == "command"
    assert dispatch.route("Hey Vector", asleep=True).text == ""


def test_table_shape():
    assert dispatch.ORDER == ["sleep", "halt", "wake", "asleep_gate", "ssh", "command", "pet", "music", "face", "brain"]
    assert dispatch.ASLEEP_OK == {"sleep", "wake", "asleep_gate"}


def test_intent_category_order():
    import intents
    prios = [intents._prio(c[0]) for c in intents._COMPILED]
    assert prios == sorted(prios)
