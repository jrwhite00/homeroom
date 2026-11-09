"""Download the published standards PDFs, extract and parse them, and index one document per standard.

    cd backend && python -m app.load_standards [--recreate] [--dry-run] [--pdf-dir DIR]

No standards text is kept in the repo (the repo is public, and CSTA is CC BY-NC-SA 4.0). The PDFs
go into data/standards/cache/, which git ignores:
  - Utah PDFs are downloaded from schools.utah.gov the first time.
  - The CSTA site blocks scripted downloads, so save its PDF from a browser first. The error
    message says where to get it and where to put it.
Every PDF must match the SHA-256 below, so the parser always sees the text it was written for.
After parsing, each source must give the expected number of standards, or nothing is indexed.

Connection comes from the environment, never from arguments, so the key stays out of shell history:
    ES_URL      default http://localhost:9200 (falls back to ES_LOCAL_URL)
    ES_API_KEY  (falls back to ES_LOCAL_API_KEY, the name start-local uses)

After loading it compares each source's _count with the number parsed and exits 1 if any differ.
It uses _count rather than _cat/indices because semantic_text chunks inflate docs.count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.standards import parse_source

INDEX = "standards"
DEFAULT_INFERENCE = ".elser-2-elasticsearch"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "standards" / "cache"
USER_AGENT = "homeroom-standards-loader"
CSTA_PAGE = "https://csteachers.org/pk12standards/"


@dataclass(frozen=True)
class Source:
    name: str          # also the cached file name, <name>.pdf
    url: str | None    # None when the publisher blocks scripted downloads
    sha256: str
    expected: int      # standards the parser finds in this exact PDF


SOURCES = [
    Source("csta-2026-pk12-cs-standards", None,
           "fa402f55fd5690e7676e9f8578435a1b836b65cbf440085f1959a051f0b2f41f", 331),
    Source("utah-6-12-cs-standards",
           "https://schools.utah.gov/cte/_cte/computer/612ComputerScienceStandards.pdf",
           "e0001dfa6e6a13621138678603c1aa10de6f757da993bd001badb37a62adca43", 51),
    Source("utah-cte-computer-programming-1",
           "https://schools.utah.gov/cte/_cte/strands/ComputerProgramming1.pdf",
           "d8597c8a6ca644d89c65d68f227bb4877101f0342901ac546bad39397f7dd396", 17),
    Source("utah-cte-computer-programming-2",
           "https://schools.utah.gov/cte/_cte/strands/ComputerProgramming2.pdf",
           "6c737a17e49ca402f8e94a043a6f7fc30460ff3593518966e964f3ba68020cde", 17),
    Source("utah-cte-computer-science-principles",
           "https://www.schools.utah.gov/cte/_cte/strands/ComputerSciencePrinciples.pdf",
           "456ad21502b8d9b21dea2ee24a26ee6d6d160afdb31b79bdbc66f54548a6abc9", 20),
    Source("utah-cte-exploring-computer-science",
           "https://schools.utah.gov/cte/_cte/strands/ExploringComputerScience.pdf",
           "3671c9a0a7d9663efddfdd9175d35739cc8cf4f26ec1e8f315729e292d6029df", 26),
    Source("utah-cte-web-development-1",
           "https://schools.utah.gov/cte/_cte/strands/WebDevelopment1.pdf",
           "05ad76c740d0a720738d9acb3b55a087cba02633db1561934b796ea803bb5683", 18),
]


class LoadError(SystemExit):
    """Stops the loader with a message and exit code 1."""

    def __init__(self, message: str):
        super().__init__(message)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def fetch(source: Source, pdf_dir: Path, downloader=download) -> Path:
    """Return the path of the source's PDF, downloading it if needed, after checking its hash."""
    path = pdf_dir / f"{source.name}.pdf"
    if not path.exists():
        if source.url is None:
            raise LoadError(
                f"{path.name} is missing. Its publisher blocks scripted downloads, so open {CSTA_PAGE} "
                f"in a browser, download the 2026 CSTA PK-12 Computer Science Standards PDF, and save it as "
                f"{path}")
        print(f"downloading {source.url}", flush=True)
        downloader(source.url, path)
    actual = sha256_of(path)
    if actual != source.sha256:
        raise LoadError(
            f"{path.name} has SHA-256 {actual}, expected {source.sha256}. The publisher may have changed "
            f"the document. Delete the file to download it again; if it still differs, check the parser "
            f"against the new PDF and update SOURCES in a pull request.")
    return path


def extract_text(path: Path) -> str:
    """Text of every page, joined with newlines. pypdf is only needed here, at load time."""
    import pypdf

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings on every CSTA page
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(path).pages)


def parse_all(pdf_dir: Path, extract=extract_text, downloader=download) -> dict[str, list[dict]]:
    """Return {source: [docs]}. Stops before indexing if any source gives an unexpected count."""
    by_source = {s.name: parse_source(s.name, extract(fetch(s, pdf_dir, downloader))) for s in SOURCES}
    wrong = [f"{s.name}: parsed {len(by_source[s.name])}, expected {s.expected}"
             for s in SOURCES if len(by_source[s.name]) != s.expected]
    if wrong:
        raise LoadError("Unexpected number of standards, nothing indexed:\n  " + "\n  ".join(wrong))
    return by_source


def mapping(inference_id: str = DEFAULT_INFERENCE) -> dict:
    kw = {"type": "keyword"}
    return {
        "mappings": {
            "properties": {
                "id": kw, "source": kw, "framework": kw, "course": kw,
                "code": kw, "grade_band": kw, "strand": kw,
                "statement": {"type": "text"},
                "description": {"type": "text", "copy_to": "semantic"},
                "semantic": {"type": "semantic_text", "inference_id": inference_id},
            }
        }
    }


class ES:
    def __init__(self, url: str, api_key: str | None):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}

    def req(self, method: str, path: str, body=None, ndjson: bool = False, ok404: bool = False, timeout: int = 900):
        headers = dict(self.headers)
        data = None
        if body is not None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            headers["Content-Type"] = "application/x-ndjson" if ndjson else "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if ok404 and e.code == 404:
                return None
            raise LoadError(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:1000]}")


def bulk_body(index: str, docs: list[dict]) -> str:
    return "".join(json.dumps({"index": {"_index": index, "_id": d["id"]}}) + "\n" + json.dumps(d) + "\n"
                   for d in docs)


def load(es: ES, by_source: dict[str, list[dict]], recreate: bool, inference_id: str, batch: int = 50) -> None:
    exists = es.req("HEAD", f"/{INDEX}", ok404=True) is not None
    if exists and recreate:
        es.req("DELETE", f"/{INDEX}")
        exists = False
    if not exists:
        es.req("PUT", f"/{INDEX}", mapping(inference_id))
        print(f"created {INDEX}")
    docs = [d for source_docs in by_source.values() for d in source_docs]
    for i in range(0, len(docs), batch):
        res = es.req("POST", "/_bulk", bulk_body(INDEX, docs[i:i + batch]), ndjson=True)
        errors = [item["index"]["error"] for item in res.get("items", []) if item["index"].get("error")]
        if errors:
            raise LoadError(f"bulk errors: {json.dumps(errors[:3])[:1000]}")
        print(f"  {min(i + batch, len(docs))}/{len(docs)}", flush=True)
    es.req("POST", f"/{INDEX}/_refresh")


def compare_counts(es: ES, by_source: dict[str, list[dict]]) -> bool:
    """Print parsed vs indexed per source (via _count) and return True if they all match."""
    ok = True
    print(f"{'source':42} {'parsed':>6} {'index':>6}")
    for source, docs in by_source.items():
        indexed = es.req("POST", f"/{INDEX}/_count", {"query": {"term": {"source": source}}})["count"]
        ok &= indexed == len(docs)
        print(f"{source:42} {len(docs):6} {indexed:6} {'' if indexed == len(docs) else 'MISMATCH'}")
    total_parsed = sum(len(d) for d in by_source.values())
    total_indexed = es.req("GET", f"/{INDEX}/_count")["count"]
    ok &= total_indexed == total_parsed
    print(f"{'total':42} {total_parsed:6} {total_indexed:6} {'' if total_indexed == total_parsed else 'MISMATCH'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", type=Path, default=CACHE_DIR, help="folder of <source>.pdf files (git-ignored)")
    ap.add_argument("--recreate", action="store_true", help="delete and recreate the index first")
    ap.add_argument("--dry-run", action="store_true", help="fetch, parse and print counts, no Elasticsearch")
    ap.add_argument("--inference-id", default=DEFAULT_INFERENCE)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    by_source = parse_all(args.pdf_dir)
    if args.dry_run:
        for source, docs in by_source.items():
            print(f"{source:42} {len(docs):6}")
        print(f"{'total':42} {sum(len(d) for d in by_source.values()):6}")
        return 0

    es = ES(os.environ.get("ES_URL") or os.environ.get("ES_LOCAL_URL") or "http://localhost:9200",
            os.environ.get("ES_API_KEY") or os.environ.get("ES_LOCAL_API_KEY"))
    print("Elasticsearch", es.req("GET", "/")["version"]["number"], "at", es.url)
    load(es, by_source, args.recreate, args.inference_id)
    return 0 if compare_counts(es, by_source) else 1


if __name__ == "__main__":
    sys.exit(main())
