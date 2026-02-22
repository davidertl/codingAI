import subprocess
import os
import jwt
import time
import requests
from dotenv import load_dotenv

load_dotenv("/home/codingai/ai-agent/.env")

APP_ID = os.getenv("GITHUB_APP_ID")
INSTALLATION_ID = os.getenv("GITHUB_INSTALLATION_ID")
PRIVATE_KEY_PATH = "/home/codingai/ai-agent/github_app/KRT-AI-Agent.pem"

OWNER = "davidertl"
REPO = "KRT-leadtool"


def generate_jwt():
    with open(PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()

    payload = {
        "iat": int(time.time()) - 60,
        "exp": int(time.time()) + (10 * 60),
        "iss": APP_ID,
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token():
    jwt_token = generate_jwt()

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
    }

    url = f"https://api.github.com/app/installations/{int(INSTALLATION_ID)}/access_tokens"

    r = requests.post(url, headers=headers)

    if r.status_code != 201:
        raise Exception(f"Token creation failed: {r.text}")

    return r.json()["token"]


def test_push():
    print("Starting push test...")

    token = get_installation_token()
    print("Installation token acquired.")

    branch_name = "ai/app-test"

    clone_url = f"https://x-access-token:{token}@github.com/{OWNER}/{REPO}.git"

    subprocess.run(["rm", "-rf", "/home/codingai/workspaces/KRT-leadtool-test"])

    subprocess.run([
        "git", "clone",
        clone_url,
        "/home/codingai/workspaces/KRT-leadtool-test"
    ], check=True)

    repo_path = "/home/codingai/workspaces/KRT-leadtool-test"

    subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_path, check=True)

    with open(f"{repo_path}/APP_TEST.txt", "w") as f:
        f.write("GitHub App Push Test\n")

    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "App test commit"], cwd=repo_path, check=True)

    subprocess.run(
        ["git", "push", "-u", "origin", branch_name, "--force"],
        cwd=repo_path,
        check=True
    )

    print("Push successful.")


if __name__ == "__main__":
    test_push()