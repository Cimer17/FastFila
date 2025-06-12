import requests

def seed_questions():
    url = "https://z9u9-fe1r-62es.gw-1a.dockhost.net/seed_questions"
    headers = {
        "Accept": "application/json",
    }
    resp = requests.post(url, headers=headers)
    print(f"Статус: {resp.status_code}")
    print("Ответ:", resp.json())

if __name__ == "__main__":
    seed_questions()
