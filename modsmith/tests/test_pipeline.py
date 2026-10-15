from pathlib import Path

from conftest import FORGE_TOML, example_content, make_jar

from modsmith.config import Settings
from modsmith.features import extract_features
from modsmith.models import Dependency, DependencyKind, ModRef, Project, RemoteFile, Status
from modsmith.pipeline import Modsmith
from modsmith.platform import TargetInfo
from modsmith.porting import porter
from modsmith.porting.agent import Agent, AgentRun
from modsmith.porting.builder import BuildResult
from modsmith.providers.base import Provider

NEO_TOML = """
[[mods]]
modId="{mid}"
version="{ver}"
displayName="{name}"
[[dependencies.{mid}]]
modId="minecraft"
type="required"
versionRange="[26.3,26.4)"
"""


def neo_jar(path, mid="examplemod", ver="2.0.0", content=None):
    return make_jar(path, {"META-INF/neoforge.mods.toml": NEO_TOML.format(mid=mid, ver=ver, name=mid),
                           **(content if content is not None else example_content(old_layout=False))})


def rf(pid, vid, mc, loaders, deps=(), rtype="release", date="2026-10-01"):
    return RemoteFile(provider="modrinth", project_id=pid, version_id=vid, version_number=vid,
                      filename=f"{pid}-{vid}.jar", url=f"https://cdn.example/{pid}/{vid}.jar",
                      game_versions=[mc], loaders=list(loaders), release_type=rtype,
                      dependencies=list(deps), date=date)


class FakeProvider(Provider):
    name = "modrinth"

    def __init__(self, projects, files, by_sha=None):
        self.projects, self.files, self.by_sha = projects, files, by_sha or {}

    def get_project(self, ident):
        return self.projects.get(ident)

    def search(self, query, loader=None, limit=5):
        return []

    def list_files(self, project_id, game_version=None, loaders=None):
        return [f for f in self.files.get(project_id, [])
                if (not game_version or game_version in f.game_versions)
                and (not loaders or set(f.loaders) & set(loaders))]

    def get_file(self, project_id, version_id):
        return next((f for f in self.files.get(project_id, []) if f.version_id == version_id), None)

    def identify(self, jars):
        return {j.sha1: self.by_sha[j.mod_id] for j in jars if j.mod_id in self.by_sha}


class Offline(FakeProvider):
    name = "curseforge"

    @property
    def available(self):
        return False


def make_app(tmp_path, projects, files, jars_by_vid, by_sha=None, **settings):
    s = Settings(minecraft="26.3", loader="neoforge", mods_dir=str(tmp_path / "mods"),
                 output_dir=str(tmp_path / "out"), from_minecraft="1.20.1", from_loader="forge", **settings)
    app = Modsmith(s, log=lambda m: None, cache_dir=tmp_path / "cache", workers=2)
    app.modrinth = FakeProvider(projects, files, by_sha)
    app.curseforge = Offline({}, {})
    app._target = TargetInfo("26.3", "neoforge", "26.3.0.83", 25)
    app.fetch = lambda f: jars_by_vid[f.version_id]
    return app


def proj(pid, name=None, source=None, lic="MIT"):
    return Project("modrinth", pid, pid, name or pid, source_url=source, license=lic)


def test_official_lossless_and_install(tmp_path, forge_jar):
    new = neo_jar(tmp_path / "dl" / "examplemod-2.0.0.jar")
    old_remote = rf("examplemod", "old", "1.20.1", ["forge"])
    app = make_app(tmp_path, {"examplemod": proj("examplemod")},
                   {"examplemod": [old_remote, rf("examplemod", "new", "26.3", ["neoforge"])]},
                   {"new": new}, by_sha={"examplemod": old_remote})
    results, issues = app.run([ModRef(f"file:{forge_jar}")])
    r = results[0]
    assert r.status == Status.OFFICIAL, r.warnings
    assert r.feature_diff.is_lossless
    installed = app.install(results)
    mods = Path(app.s.mods_dir)
    assert installed == ["examplemod-2.0.0.jar"]
    assert (mods / "examplemod-2.0.0.jar").exists() and not forge_jar.exists()
    assert list((mods / ".modsmith-backup").glob("*/examplemod-1.2.3.jar"))


def test_official_with_feature_loss_is_flagged(tmp_path, forge_jar):
    new = neo_jar(tmp_path / "dl" / "x.jar", content=example_content(old_layout=False, drop=("golem",)))
    old_remote = rf("examplemod", "old", "1.20.1", ["forge"])
    app = make_app(tmp_path, {"examplemod": proj("examplemod")},
                   {"examplemod": [old_remote, rf("examplemod", "new", "26.3", ["neoforge"])]}, {"new": new},
                   by_sha={"examplemod": old_remote})
    r = app.run([ModRef(f"file:{forge_jar}")])[0][0]
    assert r.status == Status.OFFICIAL_FEATURE_LOSS
    assert r.feature_diff.missing["entities"] == ["examplemod:golem"]
    assert any("missing content" in w for w in r.warnings)


def test_up_to_date_and_missing_dependency_auto_added(tmp_path):
    lib_new = neo_jar(tmp_path / "dl" / "lib.jar", mid="corelib", content={})
    app_new = neo_jar(tmp_path / "dl" / "app.jar")
    dep = Dependency("corelib", DependencyKind.REQUIRED, provider="modrinth", project_id="corelib")
    files = {"examplemod": [rf("examplemod", "a26", "26.3", ["neoforge"], deps=[dep])],
             "corelib": [rf("corelib", "c26", "26.3", ["neoforge"])]}
    app = make_app(tmp_path, {"examplemod": proj("examplemod"), "corelib": proj("corelib")}, files,
                   {"a26": app_new, "c26": lib_new})
    results, issues = app.run([ModRef("modrinth:examplemod", pinned_version="a26")])
    by = {r.ref.spec: r for r in results}
    assert by["modrinth:examplemod"].status == Status.UP_TO_DATE
    assert by["modrinth:corelib"].status == Status.OFFICIAL  # new install, nothing to compare against
    assert "Added automatically" in by["modrinth:corelib"].notes[0]
    assert not [i for i in issues if i.problem == "missing"]
    assert sorted(app.install(results)) == ["app.jar", "lib.jar"]


def test_no_official_without_source_or_decompile(tmp_path, forge_jar):
    old_remote = rf("examplemod", "old", "1.20.1", ["forge"])
    app = make_app(tmp_path, {"examplemod": proj("examplemod")}, {"examplemod": [old_remote]}, {},
                   by_sha={"examplemod": old_remote})
    results, issues = app.run([ModRef(f"file:{forge_jar}")], do_port=False)
    assert results[0].status == Status.NO_OFFICIAL
    # the jar's displayURL points at github, so porting would try it; make it unreachable
    app.s.allow_decompile = False
    import modsmith.sources as src
    orig = src.remote_refs
    src.remote_refs = lambda url: (_ for _ in ()).throw(RuntimeError("offline"))
    try:
        results, _ = app.run([ModRef(f"file:{forge_jar}")], do_port=True)
    finally:
        src.remote_refs = orig
    assert results[0].status == Status.NO_SOURCE
    assert any("allow_decompile" in n for n in results[0].notes)


def test_unresolved(tmp_path):
    app = make_app(tmp_path, {}, {}, {})
    r = app.run([ModRef("modrinth:does-not-exist")])[0][0]
    assert r.status == Status.UNRESOLVED


# ------------------------------------------------------------- port loop gates
class ScriptedAgent(Agent):
    """Each pass 'edits' the project by choosing what the next build produces."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.prompts = []

    def run(self, prompt, workdir, env=None, log_file=None, timeout=0):
        self.prompts.append(prompt)
        (Path(workdir) / "pass.txt").write_text(str(len(self.prompts)))
        return AgentRun(True, "ok")


def test_port_loop_requires_build_metadata_and_features(tmp_path, forge_jar, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    full = neo_jar(tmp_path / "b" / "full.jar")
    partial = neo_jar(tmp_path / "b" / "partial.jar", content=example_content(old_layout=False, drop=("ruby_sword",)))
    wrong_mc = make_jar(tmp_path / "b" / "old.jar", {"META-INF/mods.toml": FORGE_TOML, **example_content()})
    builds = iter([
        BuildResult(False, "x", errors=["Foo.java:1: error: cannot find symbol"]),
        BuildResult(True, "", jars=[wrong_mc]),
        BuildResult(True, "", jars=[partial]),
        BuildResult(True, "", jars=[full]),
    ])
    monkeypatch.setattr(porter, "run_gradle", lambda *a, **k: next(builds))
    monkeypatch.setattr(porter, "find_java", lambda *a, **k: Path("/jdk25"))
    agent = ScriptedAgent([])
    res = porter.port(workdir=work, name="Example", mod_id="examplemod", from_mc="1.20.1", from_loader="forge",
                      target=TargetInfo("26.3", "neoforge", "26.3.0.83", 25),
                      baseline=extract_features(forge_jar), agent=agent, java_homes={}, max_attempts=6,
                      log=lambda m: None)
    assert res.ok and res.attempts == 4 and res.jar == full
    assert "NON-NEGOTIABLE" in agent.prompts[0] and "Java 25" in agent.prompts[0]
    assert "cannot find symbol" in agent.prompts[1]
    assert "metadata is wrong" in agent.prompts[2]
    assert "examplemod:ruby_sword" in agent.prompts[3]
    assert (work / ".modsmith" / "baseline-features.json").exists()


def test_port_loop_gives_up(tmp_path, forge_jar, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(porter, "run_gradle", lambda *a, **k: BuildResult(False, "boom", errors=["e"]))
    monkeypatch.setattr(porter, "find_java", lambda *a, **k: Path("/jdk25"))
    res = porter.port(workdir=work, name="E", mod_id="examplemod", from_mc="1.20.1", from_loader="forge",
                      target=TargetInfo("26.3", "neoforge", None, 25), baseline=extract_features(forge_jar),
                      agent=ScriptedAgent([]), java_homes={}, max_attempts=2, log=lambda m: None)
    assert not res.ok and res.attempts == 2


def test_port_needs_jdk(tmp_path, forge_jar, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(porter, "find_java", lambda *a, **k: None)
    res = porter.port(workdir=work, name="E", mod_id="e", from_mc="1.20.1", from_loader="forge",
                      target=TargetInfo("26.3", "neoforge", None, 25), baseline=extract_features(forge_jar),
                      agent=ScriptedAgent([]), java_homes={}, log=lambda m: None)
    assert not res.ok and "JDK 25" in res.notes[0]


def test_transient_build_errors_are_retried_not_sent_to_agent(tmp_path, forge_jar, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    full = neo_jar(tmp_path / "b" / "full.jar")
    builds = iter([BuildResult(False, "Could not GET 'x'. Received status code 429 from server"),
                   BuildResult(True, "", jars=[full])])
    monkeypatch.setattr(porter, "run_gradle", lambda *a, **k: next(builds))
    monkeypatch.setattr(porter, "find_java", lambda *a, **k: Path("/jdk25"))
    monkeypatch.setattr(porter.time, "sleep", lambda s: None)
    agent = ScriptedAgent([])
    res = porter.port(workdir=work, name="E", mod_id="examplemod", from_mc="1.20.1", from_loader="forge",
                      target=TargetInfo("26.3", "neoforge", None, 25), baseline=extract_features(forge_jar),
                      agent=agent, java_homes={}, log=lambda m: None)
    assert res.ok and res.attempts == 1 and len(agent.prompts) == 1


def test_installed_port_counts_as_up_to_date(tmp_path):
    port = neo_jar(tmp_path / "mods" / "examplemod-port.jar")
    app = make_app(tmp_path, {}, {}, {})
    r = app.run([ModRef(f"file:{port}")])[0][0]
    assert r.status == Status.UP_TO_DATE
