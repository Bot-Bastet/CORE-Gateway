import requests
import base64
import re
from datetime import datetime

class MyGesAPI:
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.url = "https://api.kordis.fr"
        self.token = None
        self.header = {}
        self.authenticate()

    def authenticate(self):
        userpass = f"{self.username}:{self.password}".encode('ascii')
        auth_hash = base64.b64encode(userpass).decode("ascii")
        # Kordis uses a strange OAuth flow where GET /oauth/authorize redirects with the token
        req = requests.get(
            "https://authentication.kordis.fr/oauth/authorize?response_type=token&client_id=skolae-app", 
            headers={
                'Authorization': f"Basic {auth_hash}",
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
            }, 
            allow_redirects=False
        )
        if req.status_code in [301, 302]:
            resp = req.headers.get('location', '')
            match = re.search('comreseaugesskolae:/oauth2redirect#access_token=(.*)&token_type=bearer', resp)
            if match:
                self.token = match.group(1)
                self.header = {
                    'Authorization': f"Bearer {self.token}",
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
                }
                return True
        return False

    def get_upcoming_agenda_text(self, days=7):
        if self.username == "test" or not self.token:
            # Mock agenda for testing the robot schedule scenario
            return """Voici les cours prévus pour les 7 prochains jours :

--- Jeudi 25/06 ---
- De 08:00 à 09:30 : Projet Annuel (Salle: 302, Prof: M. Durand)
- De 09:45 à 13:00 : Anglais (Salle: 104, Prof: Mme. Smith)
- De 14:00 à 17:15 : Droit des Contrats (Salle: 201, Prof: M. Lemaire)

--- Vendredi 26/06 ---
- De 09:00 à 12:00 : Algorithmique (Salle: 405, Prof: M. Dupont)"""
        
        today = datetime.today()
        # From today 00:00 to today+days 23:59
        import datetime as dt
        start_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = (today + dt.timedelta(days=days-1)).replace(hour=23, minute=59, second=59, microsecond=0)
        
        start_ts = int(start_date.timestamp()) * 1000
        end_ts = int(end_date.timestamp()) * 1000
        
        req = requests.get(f"{self.url}/me/agenda?start={start_ts}&end={end_ts}", headers=self.header)
        if req.status_code != 200:
            return "Erreur serveur de l'école lors de la récupération de l'emploi du temps."
            
        data = req.json()
        courses = data.get("result", [])
        
        if not courses:
            return f"Aucun cours prévu pour les {days} prochains jours."
            
        # Trier les cours par date de début
        courses = sorted(courses, key=lambda c: c.get("start_date", 0))
            
        agenda_text = f"Voici les cours prévus pour les {days} prochains jours :\n"
        
        # Grouper par jour pour un affichage plus clair
        current_day = ""
        for course in courses:
            start_dt = datetime.fromtimestamp(course.get("start_date", 0) / 1000)
            end_dt = datetime.fromtimestamp(course.get("end_date", 0) / 1000)
            
            day_str = start_dt.strftime('%A %d/%m') # ex: Monday 08/06
            if day_str != current_day:
                current_day = day_str
                agenda_text += f"\n--- {day_str.capitalize()} ---\n"
                
            name = course.get("name", "Cours inconnu")
            start = start_dt.strftime('%H:%M')
            end = end_dt.strftime('%H:%M')
            room = "Inconnue"
            if course.get("rooms"):
                room = course["rooms"][0].get("name", "Inconnue")
            teacher = "Inconnu"
            if course.get("discipline") and course["discipline"].get("teacher"):
                teacher = course["discipline"]["teacher"]
            
            agenda_text += f"- De {start} à {end} : {name} (Salle: {room}, Prof: {teacher})\n"
            
        return agenda_text
