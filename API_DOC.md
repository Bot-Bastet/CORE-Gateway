# Documentation API Bastet Gateway

Le serveur centralisé de la passerelle tourne en **HTTPS** sur le port `8001`. 
Il sert de pont de stockage sécurisé entre l'application mobile et le robot (Agent IA CORE).

> **Important** : Le certificat SSL est **auto-signé**. Les clients effectuant les requêtes (app mobile, python CORE) doivent ignorer la validation du certificat SSL strict (`verify=False` ou équivalent).

## Authentification

Toute requête vers l'API (sauf la page d'accueil `/` qui intègre sa propre fenêtre de connexion) nécessite l'envoi du Token d'accès via le Header HTTP :

```http
X-API-Token: <VOTRE_TOKEN_SECRET_DANS_LE_FICHIER_ENV>
```

---

## 1. État du système CORE (`/core/state`)

Permet au robot de diffuser en direct ce qu'il a devant lui, ainsi que ses derniers échanges de messages avec l'utilisateur, et permet à l'App Mobile de lire cet état.

### Mettre à jour l'état (Robot -> Gateway)
**POST** `https://GATEWAY_IP:8001/core/state`

**Body (JSON)**
```json
{
  "seen_person": "Teano",
  "seen_objects": ["ordinateur", "bouteille"],
  "last_chat": [
    {"role": "user", "content": "Que vois-tu ?"},
    {"role": "assistant", "content": "Je vois Teano et un ordinateur."}
  ],
  "robot_status": "listening" 
}
```
*Note : `robot_status` peut être "idle", "listening", "speaking", "moving", etc.*

### Récupérer l'état (App Mobile -> Gateway)
**GET** `https://GATEWAY_IP:8001/core/state`

---

## 2. Identifiants Intranet MyGES (`/myges`)

Permet à l'utilisateur de fournir via son application mobile ses identifiants MyGES, que le robot pourra ensuite récupérer de manière protégée pour aller scrapper son agenda ou ses notes.

### Sauvegarder les identifiants (App Mobile -> Gateway)
**POST** `https://GATEWAY_IP:8001/myges`

**Body (JSON)**
```json
{
  "username": "mon_id_myges",
  "password": "mon_mot_de_passe"
}
```

### Récupérer les identifiants (Robot -> Gateway)
**GET** `https://GATEWAY_IP:8001/myges`

*(Renvoie l'objet JSON ci-dessus avec un champ `updated_at` additionnel).*

---

## 3. Reconnaissance Faciale (`/faces`)

Gestions des visages connus par le robot. Utilisé par la page d'accueil de la gateway ou par le robot lui-même ou l'app.

### Lister les visages
**GET** `https://GATEWAY_IP:8001/faces`
*(Supporte le query parameter `?name=Bob` pour filtrer).*

### Sauvegarder un nouveau visage
**POST** `https://GATEWAY_IP:8001/faces/upload`
**Body (FormData / Multipart)** :
- `name` (Texte) : Le nom de la personne
- `file` (Fichier) : L'image (.jpg, .png...)

### Télécharger l'image d'un visage
**GET** `https://GATEWAY_IP:8001/faces/{id}/image`

### Supprimer un visage
**DELETE** `https://GATEWAY_IP:8001/faces/{id}`

---

## 4. Système (`/health`)

Vérifier si le serveur réponds correctement en HTTPS.
**GET** `https://GATEWAY_IP:8001/health` (Ne requiert pas de token).
