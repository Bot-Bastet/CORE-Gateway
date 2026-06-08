import os
import glob
import requests
import json

GATEWAY_URL = "http://localhost:8001"
API_TOKEN = "your-api-token-here"
HEADERS = {"X-API-Token": API_TOKEN}

USER_NAME = "Téano Krywak"

def create_admin_account():
    print(f"Création du compte administrateur pour {USER_NAME}...")
    account_info = {
        "email": "teanokry@gmail.com",
        "pseudo": "tealo",
        "last_name": "Krywak",
        "first_name": "Téano",
        "phone": "0769362422",
        "is_admin": True
    }
    r = requests.post(f"{GATEWAY_URL}/accounts", json=account_info, headers=HEADERS)
    if r.status_code == 200:
        print("✅ Compte administrateur créé/mis à jour avec succès.")
    else:
        print("❌ Erreur lors de la création du compte:", r.text)

def save_myges_credentials():
    print(f"Enregistrement des identifiants MyGES pour {USER_NAME}...")
    creds = {
        "username": "t.krywak",
        "password": "6faZhE3V"
    }
    r = requests.post(f"{GATEWAY_URL}/myges?name={USER_NAME}", json=creds, headers=HEADERS)
    if r.status_code == 200:
        print("✅ Identifiants MyGES sauvegardés.")
    else:
        print("❌ Erreur MyGES:", r.text)

def upload_faces():
    print(f"Upload des visages pour {USER_NAME} (max 8)...")
    pictures_dir = r"C:\Users\Teano\Pictures\Saved Pictures"
    if not os.path.exists(pictures_dir):
        print(f"❌ Le dossier {pictures_dir} n'existe pas.")
        return

    # Extensions supportées
    files = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.bmp"):
        files.extend(glob.glob(os.path.join(pictures_dir, ext)))
    
    if not files:
        print("⚠️ Aucune image trouvée dans le dossier.")
        return
    
    # On limite à 8
    files = files[:8]
    print(f"📸 {len(files)} image(s) trouvée(s). Envoi en cours...")

    for file_path in files:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            files_payload = {"file": (filename, f, "image/jpeg")}
            r = requests.post(f"{GATEWAY_URL}/faces/upload?name={USER_NAME}", files=files_payload, headers=HEADERS)
            
            if r.status_code == 200:
                print(f"  ✅ {filename} uploadée.")
            elif r.status_code == 400 and "Limite atteinte" in r.text:
                print(f"  🛑 {filename} ignorée (limite de 8 photos atteinte).")
            else:
                print(f"  ⚠️ {filename}: {r.json().get('msg', r.text)}")

if __name__ == "__main__":
    print("📱 --- Démarrage du Simulateur Mobile ---")
    create_admin_account()
    save_myges_credentials()
    upload_faces()
    print("📱 --- Terminé ---")
