"""Restore the prepared source bundle and publish verified archival assets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    uploaded_commit = os.environ["GITHUB_SHA"]
    transport = Path(".archive-upload")
    manifest = json.loads((transport / "manifest.json").read_text())
    if manifest["repository"] != repository:
        raise RuntimeError("Archive destination does not match this repository")

    with tempfile.TemporaryDirectory(prefix="hackathon-archive-") as temporary:
        temporary = Path(temporary)
        assets = []
        for item in manifest["assets"]:
            destination = temporary / item["name"]
            if destination.parent != temporary:
                raise RuntimeError("Unexpected asset filename")
            with destination.open("wb") as output:
                for part in item["parts"]:
                    source = transport / part
                    if not source.resolve().is_relative_to(transport.resolve()):
                        raise RuntimeError("Unexpected archive part path")
                    with source.open("rb") as input_file:
                        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                            output.write(chunk)
            if destination.stat().st_size != item["size"] or sha256(destination) != item["sha256"]:
                raise RuntimeError("Checksum mismatch: " + item["name"])
            print(f"Verified {item['name']}: {item['size']} bytes", flush=True)
            assets.append(destination)

        bundle = next(path for path in assets if path.suffix == ".bundle")
        run("git", "bundle", "verify", str(bundle))
        run("git", "fetch", str(bundle), "refs/heads/main:refs/remotes/archive/main")
        source_head = run("git", "rev-parse", "refs/remotes/archive/main")
        if source_head != manifest["source_head"]:
            raise RuntimeError("Source bundle head does not match the manifest")
        run("git", "fetch", "origin", "main")
        remote_head = run("git", "rev-parse", "FETCH_HEAD")
        source_tree = run("git", "rev-parse", source_head + "^{tree}")
        environment = dict(os.environ)
        environment.update({
            "GIT_AUTHOR_NAME": "itsZENR",
            "GIT_AUTHOR_EMAIL": "24351126+itsZENR@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "itsZENR",
            "GIT_COMMITTER_EMAIL": "24351126+itsZENR@users.noreply.github.com",
        })
        # The second parent preserves the remote bootstrap, allowing a normal
        # fast-forward publication without overwriting remote work.
        final_head = run("git", "commit-tree", source_tree, "-p", source_head,
                         "-p", remote_head, "-m", "Publish preserved hackathon archive", env=environment)
        run("git", "push", "origin", final_head + ":refs/heads/main")
        print("Published source history:", final_head, flush=True)

        checksums = temporary / "SHA256SUMS.txt"
        checksums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in assets))
        assets.append(checksums)
        description = temporary / "release-notes.md"
        description.write_text(manifest["release_notes"], encoding="utf-8")
        tag = manifest["tag"]
        existing = subprocess.run(["gh", "release", "view", tag, "--repo", repository],
                                  text=True, capture_output=True)
        if existing.returncode:
            run("gh", "release", "create", tag, "--repo", repository, "--draft",
                "--target", final_head, "--title", manifest["title"], "--notes-file", str(description))
        run("gh", "release", "upload", tag, "--repo", repository, "--clobber", *(str(path) for path in assets))
        published_assets = json.loads(run("gh", "api", f"repos/{repository}/releases/tags/{tag}"))
        by_name = {asset["name"]: asset["size"] for asset in published_assets["assets"]}
        for asset in assets:
            if by_name.get(asset.name) != asset.stat().st_size:
                raise RuntimeError("Release asset verification failed: " + asset.name)
        run("gh", "release", "edit", tag, "--repo", repository, "--draft=false")
        print(f"Release verified: https://github.com/{repository}/releases/tag/{tag}", flush=True)

    current_upload = run("gh", "api", f"repos/{repository}/git/ref/heads/archive-upload", "--jq", ".object.sha")
    if current_upload == uploaded_commit:
        run("gh", "api", "--method", "DELETE", f"repos/{repository}/git/refs/heads/archive-upload")
        print("Removed the temporary upload branch", flush=True)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(f"Source and all archival assets verified.\n\n[Download the archive](https://github.com/{repository}/releases/tag/{tag})\n")


if __name__ == "__main__":
    main()
