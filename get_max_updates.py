import requests
import os

MAX_TOKEN = "f9LHodD0cOKHe0YPWWmy6ORsdO1eAm-_nizivJ_Xw9Wk7ZzszxHljEHBTt7TMS7WVhtFP31kSFMrQcZLEklp"

url = "https://platform-api.max.ru/updates"

params = {
    "types": "message_created",
    "limit": 50
}

headers = {
    "Authorization": MAX_TOKEN
}

resp = requests.get(url, params=params, headers=headers)

print(resp.status_code)
print(resp.text)