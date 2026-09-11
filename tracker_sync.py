"""
SDE-2 Prep Tracker - Google Gemini & Colab Notebook Sync Client ("Switch")
========================================================================
Synchronizes roadmap status, DSA problems, and System Design topics
directly between your "Switch" notebook and your iPhone tracker web app
via GitHub (harshansarvaiya/tracker).

Zero external dependencies required (uses standard Python library).

Usage in your "Switch" Notebook:
--------------------------------
# 1. Download or import tracker_sync
!curl -s -O https://raw.githubusercontent.com/harshansarvaiya/tracker/main/tracker_sync.py
from tracker_sync import Tracker

# 2. Connect with your GitHub Personal Access Token (PAT)
t = Tracker(token="ghp_yourTokenHere")

# 3. Mark problems done, add topics, inspect status
t.done("974")                          # Marks LC 974 as DONE & syncs immediately to iPhone
t.in_progress("LC 75")                 # Marks LC 75 as IN_PROGRESS
t.add_dsa("LC 200", "Number of Islands", "Graph BFS", notes="Grid traversal with visited set")
t.status()                             # Prints live progress dashboard
"""

import json
import base64
import os
import sys
import re
from datetime import datetime, timezone
import urllib.request
import urllib.error
import urllib.parse


class Tracker:
    def __init__(self, token=None, repo="harshansarvaiya/tracker", branch="main", file_path="data.json"):
        """
        Initializes the Tracker sync client.
        :param token: GitHub Personal Access Token (classic with repo scope, or fine-grained with contents:read/write)
        :param repo: GitHub repository path ('owner/repo')
        :param branch: Branch name (default 'main')
        :param file_path: Relative path to data.json in repo (default 'data.json')
        """
        self.repo = repo.strip()
        self.branch = branch.strip()
        self.file_path = file_path.strip()

        # Token resolution: direct arg -> Colab secrets -> environment variable
        self.token = token
        if not self.token:
            # Try Google Colab userdata secret
            try:
                from google.colab import userdata
                self.token = userdata.get("GITHUB_TOKEN")
            except Exception:
                pass
        if not self.token:
            self.token = os.environ.get("GITHUB_TOKEN")

        self.current_sha = None
        self.data = None
        self.pull()

    # --------------------------------------------------------------------------
    # GITHUB API CLIENT
    # --------------------------------------------------------------------------
    def _headers(self, write=False):
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Switch-Notebook-Tracker-Sync/1.0"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        elif write:
            raise ValueError(
                "GitHub Personal Access Token is required to push updates to tracker.\n"
                "Provide it via Tracker(token='ghp_...') or in Colab Secrets as 'GITHUB_TOKEN'."
            )
        return headers

    def pull(self):
        """Fetches the latest data.json from GitHub."""
        url = f"https://api.github.com/repos/{self.repo}/contents/{self.file_path}?ref={self.branch}"
        req = urllib.request.Request(url, headers=self._headers(write=False))
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                self.current_sha = result.get("sha")
                raw_content = base64.b64decode(result.get("content", "")).decode("utf-8")
                self.data = json.loads(raw_content)
                return self.data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(f"'{self.file_path}' not found in repo {self.repo} on branch {self.branch}.")
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"GitHub API error ({e.code}): {body}")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch data from GitHub: {str(e)}")

    def _push(self, commit_message):
        """Pushes current in-memory self.data back to GitHub."""
        if not self.token:
            raise ValueError("Cannot push without a GitHub Personal Access Token (PAT).")

        self.data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
        self.data["updatedBy"] = "Switch Notebook"
        
        # Keep recentCommands log capped at 20 entries
        if "recentCommands" not in self.data or not isinstance(self.data["recentCommands"], list):
            self.data["recentCommands"] = []
        self.data["recentCommands"].append({
            "command": commit_message,
            "timestamp": self.data["lastUpdated"]
        })
        self.data["recentCommands"] = self.data["recentCommands"][-20:]

        content_bytes = json.dumps(self.data, indent=2).encode("utf-8")
        encoded_content = base64.b64encode(content_bytes).decode("utf-8")

        url = f"https://api.github.com/repos/{self.repo}/contents/{self.file_path}"
        payload = {
            "message": commit_message,
            "content": encoded_content,
            "sha": self.current_sha,
            "branch": self.branch
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(write=True),
            method="PUT"
        )

        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                self.current_sha = res.get("content", {}).get("sha")
                print(f"  Synced to GitHub & iPhone: {commit_message}")
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            # If SHA conflict, auto-refresh and retry once
            if e.code == 409:
                print("  [Conflict detected: auto-refreshing latest state and retrying...]")
                self.pull()
                payload["sha"] = self.current_sha
                req2 = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=self._headers(write=True),
                    method="PUT"
                )
                with urllib.request.urlopen(req2) as resp2:
                    res2 = json.loads(resp2.read().decode("utf-8"))
                    self.current_sha = res2.get("content", {}).get("sha")
                    print(f"  Synced to GitHub & iPhone: {commit_message}")
                    return True
            raise RuntimeError(f"GitHub Push failed ({e.code}): {body}")

    # --------------------------------------------------------------------------
    # FINDER HELPERS
    # --------------------------------------------------------------------------
    def _find_item(self, query):
        """Finds item across both DSA problems and SD topics."""
        q = str(query).strip().lower()
        clean_num = re.sub(r"[^\d]", "", q)

        # 1. Search DSA
        for p in self.data.get("dsa", []):
            if clean_num and (str(p.get("id")) == clean_num or re.sub(r"[^\d]", "", p.get("code", "")) == clean_num):
                return ("dsa", p)
            if q in p.get("title", "").lower() or q in p.get("code", "").lower():
                return ("dsa", p)

        # 2. Search SD
        for t in self.data.get("sd", []):
            if q == str(t.get("id", "")).lower() or q == str(t.get("code", "")).lower():
                return ("sd", t)
            if q in t.get("title", "").lower():
                return ("sd", t)

        return (None, None)

    # --------------------------------------------------------------------------
    # STATUS MUTATION METHODS
    # --------------------------------------------------------------------------
    def set_status(self, target, status):
        """Sets status of a DSA problem or SD topic to 'DONE', 'IN_PROGRESS', or 'TODO'."""
        status = status.upper()
        if status not in ("DONE", "IN_PROGRESS", "TODO"):
            raise ValueError("Status must be one of: 'DONE', 'IN_PROGRESS', 'TODO'")

        kind, item = self._find_item(target)
        if not item:
            raise ValueError(f"Could not find problem or topic matching '{target}'. Use t.status() to inspect.")

        old_status = item.get("status")
        item["status"] = status
        title = item.get("code", item.get("title"))
        commit_msg = f"[Switch Notebook] Update {title} status to {status} (was {old_status})"
        self._push(commit_msg)
        return item

    def done(self, target):
        """Mark a problem or topic as DONE."""
        return self.set_status(target, "DONE")

    def in_progress(self, target):
        """Mark a problem or topic as IN_PROGRESS."""
        return self.set_status(target, "IN_PROGRESS")

    def todo(self, target):
        """Mark a problem or topic as TODO."""
        return self.set_status(target, "TODO")

    # --------------------------------------------------------------------------
    # ADDING PROBLEMS / TOPICS
    # --------------------------------------------------------------------------
    def add_dsa(self, code, title, pattern="General", status="TODO", notes="", link=None, id=None):
        """Adds a new DSA problem to the tracker."""
        digits = re.sub(r"[^\d]", "", str(code))
        prob_id = str(id or (digits if digits else title.lower().replace(" ", "-")))
        
        # Check if already exists
        for p in self.data.get("dsa", []):
            if str(p.get("id")) == prob_id:
                print(f"Problem {prob_id} already exists ({p.get('title')}). Updating details instead.")
                p["title"] = title
                p["pattern"] = pattern
                p["status"] = status
                if notes:
                    p["notes"] = notes
                if link:
                    p["link"] = link
                self._push(f"[Switch Notebook] Update DSA: {p.get('code', prob_id)} {title}")
                return p

        item = {
            "id": prob_id,
            "code": code if "LC" in code.upper() else f"LC {code}",
            "title": title.strip(),
            "pattern": pattern.strip(),
            "status": status.upper(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "link": link or f"https://leetcode.com/problemset/all/?search={urllib.parse.quote(title)}",
            "notes": notes.strip()
        }
        self.data["dsa"].append(item)
        self._push(f"[Switch Notebook] Add DSA: {item['code']} {title}")
        return item

    def add_sd(self, title, track="LLD", code=None, status="TODO", notes="", link=None, id=None):
        """Adds a new System Design topic (LLD or HLD)."""
        track = track.upper()
        if track not in ("LLD", "HLD"):
            track = "LLD"

        if not code:
            existing_count = len([t for t in self.data.get("sd", []) if t.get("track") == track])
            code = f"{track}-{str(existing_count + 1).zfill(2)}"

        topic_id = id or code.lower().replace(" ", "-")

        item = {
            "id": topic_id,
            "code": code,
            "title": title.strip(),
            "track": track,
            "status": status.upper(),
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "link": link or "https://github.com/harshansarvaiya/SystemDesign",
            "notes": notes.strip()
        }
        self.data["sd"].append(item)
        self._push(f"[Switch Notebook] Add SD: {code} {title} ({track})")
        return item

    # --------------------------------------------------------------------------
    # DELETING ITEMS
    # --------------------------------------------------------------------------
    def delete_dsa(self, target):
        """Deletes a DSA problem by ID, code, or title."""
        kind, item = self._find_item(target)
        if not item or kind != "dsa":
            raise ValueError(f"Could not find DSA problem matching '{target}'.")
        self.data["dsa"] = [p for p in self.data["dsa"] if p.get("id") != item.get("id")]
        self._push(f"[Switch Notebook] Delete DSA: {item.get('code', item.get('id'))} {item.get('title')}")
        return item

    def delete_sd(self, target):
        """Deletes a System Design topic by ID, code, or title."""
        kind, item = self._find_item(target)
        if not item or kind != "sd":
            raise ValueError(f"Could not find SD topic matching '{target}'.")
        self.data["sd"] = [t for t in self.data["sd"] if t.get("id") != item.get("id")]
        self._push(f"[Switch Notebook] Delete SD: {item.get('code', item.get('id'))} {item.get('title')}")
        return item

    def set_streak(self, days):
        """Updates the streak count."""
        days = int(days)
        self.data["streak"] = days
        self._push(f"[Switch Notebook] Update streak to {days} days")
        return days

    # --------------------------------------------------------------------------
    # NATURAL COMMAND INTERPRETER
    # --------------------------------------------------------------------------
    def run(self, command_str):
        """
        Executes natural commands directly from the notebook cell.
        Examples:
          t.run("done 974")
          t.run("in progress 75")
          t.run("add dsa LC 200 Number of Islands, Graph BFS")
          t.run("status")
        """
        cmd = command_str.strip()
        cmd_lower = cmd.lower()

        if cmd_lower in ("status", "summary", "progress", "audit"):
            return self.status()

        if cmd_lower.startswith("done ") or cmd_lower.startswith("mark done "):
            target = re.sub(r"^(done|mark done)\s+", "", cmd, flags=re.IGNORECASE)
            return self.done(target)

        if cmd_lower.startswith("in progress ") or cmd_lower.startswith("progress "):
            target = re.sub(r"^(in progress|progress)\s+", "", cmd, flags=re.IGNORECASE)
            return self.in_progress(target)

        if cmd_lower.startswith("todo "):
            target = re.sub(r"^todo\s+", "", cmd, flags=re.IGNORECASE)
            return self.todo(target)

        if cmd_lower.startswith("add dsa "):
            content = re.sub(r"^add dsa\s+", "", cmd, flags=re.IGNORECASE)
            parts = [p.strip() for p in content.split(",")]
            code_and_title = parts[0]
            pattern = parts[1] if len(parts) > 1 else "General"
            notes = parts[2] if len(parts) > 2 else ""
            match = re.match(r"(?:LC\s*)?(\d+)\s*(.*)", code_and_title, re.IGNORECASE)
            if match:
                num, title = match.group(1), match.group(2).strip()
                return self.add_dsa(f"LC {num}", title or f"Problem {num}", pattern=pattern, notes=notes)
            return self.add_dsa(code_and_title, code_and_title, pattern=pattern, notes=notes)

        if cmd_lower.startswith("add sd ") or cmd_lower.startswith("add system design "):
            content = re.sub(r"^add (sd|system design)\s+", "", cmd, flags=re.IGNORECASE)
            parts = [p.strip() for p in content.split(",")]
            title = parts[0]
            track = parts[1].upper() if len(parts) > 1 and parts[1].upper() in ("LLD", "HLD") else "LLD"
            notes = parts[2] if len(parts) > 2 else ""
            return self.add_sd(title, track=track, notes=notes)

        if cmd_lower.startswith("delete "):
            target = re.sub(r"^delete\s+", "", cmd, flags=re.IGNORECASE)
            kind, item = self._find_item(target)
            if kind == "dsa":
                return self.delete_dsa(target)
            elif kind == "sd":
                return self.delete_sd(target)
            raise ValueError(f"Could not find item '{target}' to delete.")

        raise ValueError(f"Unrecognized command: '{command_str}'. Supported: done, in progress, todo, add dsa, add sd, status")

    # --------------------------------------------------------------------------
    # DASHBOARD & STATUS PRINTER
    # --------------------------------------------------------------------------
    def status(self):
        """Prints a visual ASCII progress dashboard in the notebook."""
        self.pull()  # Ensure freshest data
        dsa = self.data.get("dsa", [])
        sd = self.data.get("sd", [])
        streak = self.data.get("streak", 0)
        last_updated = self.data.get("lastUpdated", "Unknown")

        dsa_done = len([p for p in dsa if p.get("status") == "DONE"])
        dsa_prog = len([p for p in dsa if p.get("status") == "IN_PROGRESS"])
        sd_done = len([t for t in sd if t.get("status") == "DONE"])
        sd_prog = len([t for t in sd if t.get("status") == "IN_PROGRESS"])

        total = len(dsa) + len(sd)
        completed = dsa_done + sd_done
        pct = round((completed / total) * 100) if total > 0 else 0

        bar_len = 24
        filled = round(bar_len * (pct / 100.0))
        progress_bar = f"[{'#' * filled}{'-' * (bar_len - filled)}] {pct}%"

        print("\n" + "=" * 62)
        print("🎯 SDE-2 PREP TRACKER (Harshan • Switch Notebook Bridge)")
        print("=" * 62)
        print(f"🔥 Current Streak : {streak} days")
        print(f"🔄 Last Synced    : {last_updated} (by {self.data.get('updatedBy', 'unknown')})")
        print(f"📊 Global Progress: {progress_bar} ({completed}/{total} items)")
        print("-" * 62)
        print(f"💻 DSA Problems ({dsa_done}/{len(dsa)} Done, {dsa_prog} In Progress):")
        for p in dsa:
            st = p.get("status", "TODO")
            tag = "  ✓ DONE" if st == "DONE" else ("  ▶ PROG" if st == "IN_PROGRESS" else "  ○ TODO")
            print(f"  {tag} | {p.get('code', 'LC'):<6} | {p.get('title')[:34]:<34} | {p.get('pattern')}")

        print("-" * 62)
        print(f"🏗️ System Design Topics ({sd_done}/{len(sd)} Done, {sd_prog} In Progress):")
        for t in sd:
            st = t.get("status", "TODO")
            tag = "  ✓ DONE" if st == "DONE" else ("  ▶ PROG" if st == "IN_PROGRESS" else "  ○ TODO")
            print(f"  {tag} | {t.get('code', 'SD'):<6} | {t.get('title')[:34]:<34} | {t.get('track')}")
        print("=" * 62 + "\n")

        return {
            "completed": completed,
            "total": total,
            "percentage": pct,
            "dsa_done": dsa_done,
            "sd_done": sd_done,
            "streak": streak
        }


# Quick CLI test runner
if __name__ == "__main__":
    token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_TOKEN")
    t = Tracker(token=token)
    t.status()
