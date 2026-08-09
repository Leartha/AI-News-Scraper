from flask import Flask, render_template, request, Response
import requests
from bs4 import BeautifulSoup
from groq import Groq
import json
import os

app = Flask(__name__)

GROQ_KEY = os.environ.get("GROQ_API_KEY")

if not GROQ_KEY:
    try:
        with open("api_keys.json", "r", encoding="utf-8") as f:
            veri = json.load(f)
            GROQ_KEY = veri["groq_api_key"]
    except FileNotFoundError:
        print("⚠️ HATA: GROQ API Key bulunamadı!")

client = Groq(api_key=GROQ_KEY)

@app.route("/", methods =["GET", "POST"])


def ana_sayfa():
    ozet = None

    if request.method == "POST":
        link = request.form.get("url")
        secilen_format = request.form.get("format")

        print(f"--- SEÇİLEN FORMAT: {secilen_format} ---")
        system_prompt = "Sen uzman bir haber özetleyicisisin."

        if secilen_format == "tek_cumle":
            system_prompt = "Sana verilen metni sadece tek ve vurucu bir cümle ile özetle."
        elif secilen_format == "tweet":
            system_prompt = "Sana verilen metinden ilgi çekici, bol emojili 3 maddelik bir Tweet/X flood dizisi oluştur."
        elif secilen_format == "soru_cevap":
            system_prompt = "Sana verilen metinden en önemli 3 soruyu çıkar ve bu soruları metne göre kısaca cevapla. Format: Soru 1: ... / Cevap 1: ..."
        else:
            system_prompt = "Sana verilen metindeki reklam veya duyuru gibi detayları yok sayıp, haberin özünü Türkçe olarak maddeler halinde özetle."


        headers = {
            "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            req = requests.get(url=link, headers=headers)
            soup = BeautifulSoup(req.text, "html.parser")

            baslik_etiketi = soup.find("h1")
            baslik = baslik_etiketi.get_text().strip() if baslik_etiketi else "Haber Başlığı Bulunamadı"

            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                gorsel_url = og_image["content"]
            else:
                img_etiketi = soup.find("img")
                gorsel_url = img_etiketi["src"] if img_etiketi and img_etiketi.get("src") else None

            for cop in soup(["script", "style", "header", "footer", "nav", "aside"]):
                cop.decompose()

            tum_metin = ""
            main_icerik = soup.find("main")
            paragraflar = main_icerik.find_all(["p", "div"]) if main_icerik else soup.find_all("p")

            for p in paragraflar:
                metin = p.get_text().strip()
                if len(metin) > 30:
                    tum_metin += metin + "\n"

            response = client.chat.completions.create(
                model = "llama-3.3-70b-versatile",
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Aşağıdaki haberi özetle:\n\n{tum_metin}"}    
                ]
            )
            ozet = response.choices[0].message.content

        except Exception as e:
            ozet = f"Bir hata oluştu: {e}"
            baslik = None
            gorsel_url = None

    return render_template("index.html", ozet=ozet, baslik=baslik, gorsel_url=gorsel_url)

@app.route("/indir", methods=["POST"])
def indir():
    metin = request.form.get("ozet_metni", "")

    return Response(
        metin,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment;filename=haber_ozeti.txt"}
    )

if __name__ == '__main__':
    app.run(debug=True)