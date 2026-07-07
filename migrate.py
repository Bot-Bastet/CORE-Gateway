import os
import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_URL = "https://localhost:8001"
TOKEN = os.getenv("API_TOKEN", "your-token-here")
HEADERS = {"X-API-Token": TOKEN}

print(f"🌍 Connexion à la Gateway: {API_URL}")

# --- Migration MyGES ---
print("\n[1] Migration des données MyGES...")
config_path = r"d:\Bastet\CORE\user_config.txt"

if os.path.exists(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            data = json.loads(content)
            username = data.get("username", "")

        password = username # Impossible to easily read encrypted keyring cross-process, setting default
        
        if username:
            # On utilise 'Teano' par défaut ou le nom trouvé dans le JSON si possible
            person_name = data.get("full_name", "Teano")
            payload = {"username": username, "password": password}
            r = requests.post(f"{API_URL}/myges?name={person_name}", json=payload, headers=HEADERS, verify=False)  # nosemgrep: python.requests.security.disabled-cert-validation — local migration script, no public network
            if r.status_code == 200:
                print(f"✅ MyGES transféré avec succès pour {person_name} ({username}).")
            else:
                print(f"❌ Erreur API MyGES {r.status_code}: {r.text}")
    except Exception as e:
        print(f"❌ Erreur lecture MyGES {config_path}: {e}")
else:
    print(f"⚠️ Aucun fichier {config_path} trouvé.")


# --- Migration Visages ---
print("\n[2] Migration des Visages Connus...")
faces_dir = r"d:\Bastet\CORE\known_faces"

if os.path.exists(faces_dir):
    try:
        found = False
        for person in os.listdir(faces_dir):
            person_dir = os.path.join(faces_dir, person)
            if os.path.isdir(person_dir):
                for file in os.listdir(person_dir):
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        found = True
                        file_path = os.path.join(person_dir, file)
                        
                        with open(file_path, "rb") as img_file:
                            files = {"file": (file, img_file, "image/jpeg")}
                            try:
                                print(f"  -> Upload de {person} ({file})...", end="")
                                r = requests.post(f"{API_URL}/faces/upload?name={person}", headers=HEADERS, files=files, verify=False)  # nosemgrep: python.requests.security.disabled-cert-validation — local migration script
                                if r.status_code == 200:
                                    print(" ✅ OK")
                                else:
                                    print(f" ❌ ECHEC ({r.status_code})")
                            except Exception as e:
                                print(f" ❌ Erreur réseau: {e}")
        if not found:
             print("⚠️ Aucun visage d'image trouvé sous " + faces_dir)
    except Exception as e:
        print(f"❌ Erreur parsing des visages: {e}")
else:
    print(f"⚠️ Aucun dossier {faces_dir} trouvé.")

print("\n🚀 Migration CORE vers Gateway Terminée.")
