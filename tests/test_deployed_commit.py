from types import SimpleNamespace

from agents.services.deployed_commit import enclosing_method, frame_method, resolve

SOURCE = """namespace Dan.Plugin.Kartverket;

public class MatrikkelStoreClientService
{
    public async Task<Krets> GetKrets(long ident)
    {
        return await Fetch(ident);
    }

    public async Task<Veg> GetVeg(long ident)
    {
        var veg = await Fetch(ident);
        return veg.Value;
    }
}
"""


def test_async_state_machine_frames_name_the_method():
    assert frame_method("Ns.MatrikkelStoreClientService+<GetVeg>d__7.MoveNext") == "GetVeg"


def test_constructor_frames_name_the_class():
    assert frame_method("Ns.Dtos.SummertSkattegrunnlagDto..ctor") == "SummertSkattegrunnlagDto"


def test_plain_frames_name_the_method():
    assert frame_method("Dan.Core.Helpers.EvidenceSourceHelper.DoRequest") == "DoRequest"


def test_enclosing_method_finds_the_declaration_above_the_line():
    assert enclosing_method(SOURCE, 13) == "GetVeg"
    assert enclosing_method(SOURCE, 7) == "GetKrets"


def test_enclosing_method_is_none_past_the_end_of_the_file():
    assert enclosing_method(SOURCE, 999) is None


class _Repo:
    def __init__(self, source, sha="abc123", fail=False):
        self.source, self.sha, self.fail = source, sha, fail

    def get_commits(self, sha, until):
        if self.fail:
            raise RuntimeError("api down")
        return [SimpleNamespace(sha=self.sha)]

    def get_contents(self, path, ref):
        return SimpleNamespace(decoded_content=self.source.encode())


def _resolve(repo, line=13, symbol="Ns.Svc+<GetVeg>d__7.MoveNext"):
    return resolve(
        repo, branch="main", timestamp="2026-05-07T12:20:13.5992498Z",
        file_path="Svc.cs", line=line, symbol=symbol,
    )


def test_commit_is_used_when_the_frame_lands_in_its_method():
    pick = _resolve(_Repo(SOURCE))
    assert (pick.sha, pick.verified) == ("abc123", True)


def test_commit_is_not_used_when_the_line_moved_to_another_method():
    pick = _resolve(_Repo(SOURCE), line=7)
    assert not pick.verified
    assert "GetKrets" in pick.reason


def test_lookup_failure_falls_back_instead_of_raising():
    pick = _resolve(_Repo(SOURCE, fail=True))
    assert (pick.sha, pick.verified) == (None, False)


def test_missing_timestamp_is_not_an_error():
    pick = resolve(_Repo(SOURCE), branch="main", timestamp=None,
                   file_path="Svc.cs", line=13, symbol="Ns.Svc.GetVeg")
    assert not pick.verified
