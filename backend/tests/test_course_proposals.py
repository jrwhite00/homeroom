import base64
import io
import json
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app import course_proposals, courses_api, propose_course
from app.course_proposals import FOOTER, Proposal
from app.courses_api import get_github
from app.es import ESError
from app.search import get_es
from app.github import GitHub, GitHubError
from app.main import app

TOKEN = "ghp_secret_should_never_appear_123"


class StubGitHub(GitHub):
    """An in-memory repo: main has two concept files; branches, files and pull requests are recorded."""

    def __init__(self, fail=False):
        super().__init__("jrwhite00/homeroom", TOKEN)
        self.fail = fail
        self.calls, self.branches, self.files, self.pulls = [], {"main"}, {}, []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if self.fail:
            raise GitHubError("could not reach GitHub: URLError")
        if method == "GET" and path.startswith("/pulls?state=open&per_page=100&page="):
            page = int(path.rsplit("=", 1)[1])
            open_pulls = [p for p in self.pulls if p["state"] == "open"]
            start = (page - 1) * 100
            return [{"head": {"ref": p["head"]}} for p in open_pulls[start:start + 100]]
        if method == "GET" and path.startswith("/pulls?state=open&head=jrwhite00:"):
            head = path.split(":", 1)[1]
            return [p for p in self.pulls if p["head"] == head and p["state"] == "open"]
        if method == "GET" and path == "/contents/curriculum/concepts?ref=main":
            return [{"name": "programming-basics.yaml"}, {"name": "containers.yaml"}, {"name": "README.md"}]
        if method == "GET" and path.startswith("/contents/"):
            file_path, ref = path[len("/contents/"):].split("?ref=")
            if (ref, file_path) in self.files:
                return {"sha": "abc"}
            raise GitHubError("not found", 404)
        if method == "GET" and path == "/git/ref/heads/main":
            return {"object": {"sha": "mainsha"}}
        if method == "POST" and path == "/git/refs":
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.branches:
                raise GitHubError("Reference already exists", 422)
            self.branches.add(name)
            return {}
        if method == "PUT" and path.startswith("/contents/"):
            self.files[(body["branch"], path[len("/contents/"):])] = base64.b64decode(body["content"]).decode()
            return {}
        if method == "POST" and path == "/pulls":
            pr = {**body, "state": "open", "number": len(self.pulls) + 10,
                  "html_url": f"https://github.com/jrwhite00/homeroom/pull/{len(self.pulls) + 10}"}
            self.pulls.append(pr)
            return pr
        raise AssertionError(f"unexpected {method} {path}")


PROPOSAL = {"state": "Utah", "course": "Computer Programming 1", "standard": "Utah CTE Computer Programming 1 strands",
            "teaches": [{"slug": "programming-basics", "reason": "The strands cover variables, loops and functions."},
                        {"slug": "containers", "reason": "Strand 6 deploys a program in a container."}]}


class StubES:
    """The concepts index as loaded from main, or unreachable."""

    def __init__(self, slugs=("programming-basics", "containers"), fail=False):
        self.slugs, self.fail, self.calls = list(slugs), fail, []

    def search(self, index, body):
        self.calls.append(index)
        if self.fail:
            raise ESError("could not reach Elasticsearch")
        return {"hits": {"hits": [{"_id": s} for s in self.slugs]}}


@pytest.fixture
def es():
    return StubES()


@pytest.fixture
def client(tmp_path, monkeypatch, es):
    monkeypatch.setattr(course_proposals, "LOCAL_DIR", tmp_path / "proposed")
    monkeypatch.setattr(courses_api, "_client_proposals", {})
    gh = StubGitHub()
    app.dependency_overrides[get_github] = lambda: gh
    app.dependency_overrides[get_es] = lambda: es
    yield TestClient(app), gh, tmp_path / "proposed"
    app.dependency_overrides.clear()


def test_opens_pull_request_with_course_file_on_new_branch(client):
    c, gh, _ = client
    r = c.post("/courses/propose", json=PROPOSAL)
    assert r.status_code == 202 and r.json()["status"] == "opened" and r.json()["show_schedule"] is True
    pr = gh.pulls[0]
    assert pr["title"] == "Add course: Computer Programming 1 (Utah)"
    assert pr["head"] == "course/utah-computer-programming-1" and pr["base"] == "main"
    assert gh.files[("course/utah-computer-programming-1", "curriculum/courses/utah-computer-programming-1.yaml")] == (
        "course: Computer Programming 1\nstate: Utah\nstandard: Utah CTE Computer Programming 1 strands\n"
        "teaches: programming-basics, containers\nadded by: Homeroom\n")


def test_body_says_student_confirmed_lists_reasons_and_ends_with_teacher_line(client):
    c, gh, _ = client
    c.post("/courses/propose", json=PROPOSAL)
    body = gh.pulls[0]["body"]
    assert "after a student confirmed a match" in body and "no student data" in body
    assert "- **programming-basics**: The strands cover variables, loops and functions." in body
    assert "- **containers**: Strand 6 deploys a program in a container." in body
    assert body.endswith("A teacher must approve before this merges.")


def test_one_open_pull_request_per_course(client):
    c, gh, _ = client
    first = c.post("/courses/propose", json=PROPOSAL).json()
    second = c.post("/courses/propose", json=PROPOSAL).json()
    assert second["status"] == "already_open" and second["url"] == first["url"] and len(gh.pulls) == 1


def test_fifth_open_course_pull_request_is_the_limit(client):
    c, gh, _ = client
    gh.pulls = [{"head": f"course/existing-{n}", "state": "open"} for n in range(5)]

    response = c.post("/courses/propose", json=PROPOSAL)

    assert response.status_code == 202
    assert response.json() == {"status": "limit_reached", "url": None, "number": None,
                               "message": course_proposals.LIMIT_MESSAGE, "show_schedule": True}
    assert not any(method in ("POST", "PUT") for method, _ in gh.calls)
    assert gh.branches == {"main"}


def test_course_already_on_main_opens_nothing(client):
    c, gh, _ = client
    gh.files[("main", "curriculum/courses/utah-computer-programming-1.yaml")] = "..."
    assert c.post("/courses/propose", json=PROPOSAL).json()["status"] == "already_known" and gh.pulls == []


def test_request_has_no_place_for_the_student(client):
    c, gh, _ = client
    r = c.post("/courses/propose", json={**PROPOSAL, "student": "maya", "pseudonym": "maya"})
    assert r.status_code == 202
    everything = json.dumps(gh.pulls) + json.dumps(list(gh.files.values())) + json.dumps(gh.calls)
    assert "maya" not in everything


@pytest.mark.parametrize("change,detail", [
    ({"teaches": [{"slug": s, "reason": "r"} for s in ["programming-basics", "containers", "a", "b", "c"]]}, "at most 4"),
    ({"teaches": [{"slug": "kubernetes-basics", "reason": "r"}]}, "kubernetes-basics"),
    ({"standard": " "}, "standard"),
    ({"course": "CP1\nadded by: someone"}, "line break"),
    ({"standard": "ask maya@example.com"}, "never people"),
])
def test_refused_before_anything_is_opened(client, change, detail):
    c, gh, _ = client
    r = c.post("/courses/propose", json={**PROPOSAL, **change})
    assert r.status_code == 422 and detail in r.json()["detail"]
    assert gh.pulls == [] and not any(m in ("POST", "PUT") for m, _ in gh.calls)


def test_github_failure_saves_file_locally_and_still_shows_schedule(client):
    c, gh, local = client
    gh.fail = True
    proposal = {**PROPOSAL, "teaches": [PROPOSAL["teaches"][0]]}
    r = c.post("/courses/propose", json=proposal)
    assert r.status_code == 202 and r.json() == {"status": "saved_locally", "url": None, "number": None,
                                                  "file": "utah-computer-programming-1.yaml", "show_schedule": True}
    assert (local / "utah-computer-programming-1.yaml").read_text(encoding="utf-8").startswith("course: Computer Programming 1")


def test_local_proposal_cap_returns_202_without_writing(client, monkeypatch):
    c, gh, local = client
    gh.fail = True
    local.mkdir()
    for n in range(20):
        (local / f"proposal-{n}.yaml").write_text("existing", encoding="utf-8")
    monkeypatch.setattr("app.curriculum.read_concepts", lambda: [{"slug": "programming-basics"}, {"slug": "containers"}])

    response = c.post("/courses/propose", json=PROPOSAL)

    assert response.status_code == 202 and response.json() == {
        "status": "limit_reached", "url": None, "number": None,
        "message": course_proposals.LIMIT_MESSAGE, "show_schedule": True,
    }
    assert len(list(local.iterdir())) == 20
    assert not (local / "utah-computer-programming-1.yaml").exists()


def test_three_proposals_per_client_per_minute(client):
    c, gh, _ = client
    results = [c.post("/courses/propose", json={**PROPOSAL, "course": f"Programming {n}"}) for n in range(3)]
    blocked = c.post("/courses/propose", json={**PROPOSAL, "course": "Programming fourth"})

    assert all(response.status_code == 202 and response.json()["status"] == "opened" for response in results)
    assert blocked.status_code == 202
    assert blocked.json() == {"status": "rate_limited",
                              "message": "Please wait a minute before suggesting another course.",
                              "show_schedule": True}
    other_client = TestClient(app, client=("198.51.100.9", 12345))
    other_response = other_client.post("/courses/propose", json={**PROPOSAL, "course": "Programming elsewhere"})
    assert other_response.status_code == 202 and other_response.json()["status"] == "opened"
    assert len(gh.pulls) == 4


def test_github_failure_checks_concepts_against_the_index(client, es):
    c, gh, local = client
    gh.fail = True
    es.slugs = ["programming-basics"]
    r = c.post("/courses/propose", json=PROPOSAL)
    assert r.status_code == 422
    assert r.json()["detail"] == "These concepts aren't in the curriculum yet: containers."
    assert not (local / "utah-computer-programming-1.yaml").exists()


@pytest.mark.parametrize("es_state", ["unreachable", "empty"])
def test_github_and_index_both_unavailable_saves_nothing_but_shows_schedule(client, es, es_state):
    c, gh, local = client
    gh.fail = True
    es.fail = es_state == "unreachable"
    if es_state == "empty":
        es.slugs = []
    r = c.post("/courses/propose", json=PROPOSAL)
    assert r.status_code == 202
    assert r.json()["status"] == "not_saved" and r.json()["show_schedule"] is True
    assert "schedule" in r.json()["message"]
    assert not local.exists() or not any(local.iterdir())


def test_github_failure_uses_the_index_not_the_curriculum_folder(client, es, monkeypatch):
    # The backend image has no curriculum/ folder, so the fallback must not depend on it.
    c, gh, local = client
    gh.fail = True
    monkeypatch.setattr("app.curriculum.DEFAULT_DIR", local / "no-such-folder")
    r = c.post("/courses/propose", json=PROPOSAL)
    assert r.status_code == 202 and r.json()["status"] == "saved_locally"
    assert es.calls == ["concepts"]

def test_reused_branch_from_closed_proposal_is_updated(client):
    c, gh, _ = client
    gh.branches.add("course/utah-computer-programming-1")
    assert c.post("/courses/propose", json=PROPOSAL).json()["status"] == "opened"


def test_script_opens_same_pull_request_added_by_james(capsys):
    gh = StubGitHub()
    code = propose_course.main(["--state", "Utah", "--course", "Web Development 1", "--standard", "Utah CTE Web Development 1 strands",
                                "--teaches", "programming-basics", "--reason", "programming-basics=JavaScript basics."], gh=gh)
    assert code == 0
    pr = gh.pulls[0]
    assert pr["title"] == "Add course: Web Development 1 (Utah)" and "Opened by hand by James." in pr["body"]
    assert pr["body"].endswith(FOOTER)
    assert gh.files[("course/utah-web-development-1", "curriculum/courses/utah-web-development-1.yaml")].endswith("added by: James\n")
    assert "Opened https://github.com/jrwhite00/homeroom/pull/10" in capsys.readouterr().out


def test_from_env_uses_only_the_repository_scoped_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert GitHub.from_env()._token is None

    monkeypatch.setenv("GITHUB_TOKEN", "repository-scoped-token")
    assert GitHub.from_env()._token == "repository-scoped-token"


def test_no_token_means_no_request_and_token_never_in_errors(monkeypatch):
    with pytest.raises(GitHubError, match="no GitHub token"):
        GitHub("jrwhite00/homeroom", None).request("GET", "/pulls")

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Bad credentials", {}, io.BytesIO(b'{"message":"Bad credentials"}'))
    monkeypatch.setattr("app.github.urllib.request.urlopen", refuse)
    with pytest.raises(GitHubError) as e:
        GitHub("jrwhite00/homeroom", TOKEN).request("GET", "/pulls")
    assert e.value.status == 401 and TOKEN not in str(e.value)


def test_script_never_prints_the_token(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(course_proposals, "LOCAL_DIR", tmp_path)
    propose_course.main(["--state", "Utah", "--course", "X", "--standard", "S", "--teaches", "containers"], gh=StubGitHub())
    propose_course.main(["--state", "Utah", "--course", "Y", "--standard", "S", "--teaches", "containers"], gh=StubGitHub(fail=True))
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
