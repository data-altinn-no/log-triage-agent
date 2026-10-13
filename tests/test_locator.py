from agents.services.locator import extract_frames, pick_best_frame


def test_extract_python_frame():
    stack = '  File "agents/services/foo.py", line 42, in do_thing\n    raise ValueError()'
    frames = extract_frames(stack)
    assert len(frames) == 1
    assert frames[0].file_path == "agents/services/foo.py"
    assert frames[0].line == 42
    assert frames[0].symbol == "do_thing"


def test_extract_dotnet_frame_unix_absolute():
    stack = "   at Altinn.Api.Widget.Process() in /home/runner/work/repo/repo/src/Widget.cs:line 88"
    frames = extract_frames(stack)
    assert len(frames) == 1
    assert frames[0].file_path == "src/Widget.cs"
    assert frames[0].line == 88
    assert frames[0].symbol == "Altinn.Api.Widget.Process"


def test_extract_dotnet_frame_windows():
    stack = r"   at Foo.Bar() in C:\build\agents\src\Foo.cs:line 12"
    frames = extract_frames(stack)
    assert frames[0].file_path.endswith("src/Foo.cs")
    assert frames[0].line == 12


def test_extract_node_frame():
    stack = "    at Object.<anonymous> (/app/src/handler.js:7:11)"
    frames = extract_frames(stack)
    assert frames[0].file_path == "src/handler.js"
    assert frames[0].line == 7


def test_pick_best_skips_site_packages():
    stack = (
        '  File "/usr/lib/python3.11/site-packages/requests/api.py", line 10, in get\n'
        '  File "app/handlers.py", line 55, in handler'
    )
    frames = extract_frames(stack)
    best = pick_best_frame(frames)
    assert best is not None
    assert best.file_path == "app/handlers.py"


def test_pick_best_returns_none_on_empty():
    assert pick_best_frame([]) is None
    assert extract_frames("") == []


def test_strips_github_actions_checkout_root():
    """GitHub Actions checks out at /home/runner/work/<repo>/<repo>/.

    The generic "/work/" marker used to win here and produce
    "work/core/core/Dan.Core/..." — a path that does not exist in the clone,
    so every subsequent read failed. Real frames from data-altinn-no/core.
    """
    stack = (
        "   at Dan.Core.Services.CosmosDbAccreditationRepository.UpdateAccreditationAsync() in "
        "/home/runner/work/core/core/Dan.Core/Services/"
        "CosmosDbAccreditationRepository.cs:line 129"
    )
    best = pick_best_frame(extract_frames(stack))
    assert best is not None
    assert best.file_path == "Dan.Core/Services/CosmosDbAccreditationRepository.cs"
    assert best.line == 129


def test_actions_root_rule_requires_the_doubled_repo_name():
    """The strip is deliberately narrow: only the doubled-repo-name shape.

    Anything else under /work/ falls through to the pre-existing generic
    marker, which keeps the "work/" segment. That fallback is not obviously
    right, but no such path has been observed in real telemetry — every
    Actions frame in the 13,403-issue census used the doubled form. Asserted
    here so the narrowness is a decision on record, not an accident.
    """
    stack = "   at Dan.Core.Foo.Y() in /home/runner/work/core/other/Dan.Core/Foo.cs:line 10"
    best = pick_best_frame(extract_frames(stack))
    assert best is not None
    assert best.file_path == "work/core/other/Dan.Core/Foo.cs"


_HOST = (
    "   at Microsoft.Azure.WebJobs.Script.WebHost.WebJobsScriptHostService+<Orphan>d__60"
    ".MoveNext() in /_/src/WebJobs.Script.WebHost/WebJobsScriptHostService.cs:line 917"
)


def test_functions_host_frames_are_not_application_code():
    assert pick_best_frame(extract_frames(_HOST)) is None


def test_the_first_application_frame_wins_over_host_frames_above_it():
    app = (
        "   at Nadobe.EvidenceSources.ES_BR.UnitBasicInformation+<>c__DisplayClass5_0.<Run>b__0() "
        "in /home/runner/work/plugin-brreg/plugin-brreg/src/UnitBasicInformation.cs:line 65"
    )
    best = pick_best_frame(extract_frames(_HOST + "\n" + app))
    assert best is not None and best.line == 65
