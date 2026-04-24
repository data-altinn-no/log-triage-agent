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
