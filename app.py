import os
import json
import io
from flask import Flask, render_template, request, send_file
import requests
from bs4 import BeautifulSoup
from groq import Groq
from google import genai
from duckduckgo_search import DDGS

app = Flask(__name__)

# --- API CLIENT ÇEKİCİLERİ ---
def get_groq_client():
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        try:
            with open("api_keys.json", "r", encoding="utf-8") as f:
                key = json.load(f).get("groq_api_key")
        except Exception:
            pass
    return Groq(api_key=key) if key else None

def get_gemini_client():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        try:
            with open("api_keys.json", "r", encoding="utf-8") as f:
                key = json.load(f).get("gemini_api_key")
        except Exception:
            pass
    return genai.Client(api_key=key) if key else None

# --- HABER ÇEKİCİ ---
def haber_metni_cek(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        baslik = soup.find('h1')
        baslik_metni = baslik.get_text().strip() if baslik else ""

        og_image = soup.find('meta', property='og:image')
        gorsel_url = og_image['content'] if og_image and 'content' in og_image.attrs else ""

        for cop in soup(["script", "style", "header", "footer", "nav", "aside"]):
            cop.decompose()

        paragraflar = soup.find_all('p')
        haber_metni = " ".join([p.get_text().strip() for p in paragraflar if len(p.get_text().strip()) > 30])
        
        return baslik_metni, gorsel_url, haber_metni
    except Exception:
        return "", "", None

# --- HYBRID (YEDEKLI) ÖZETLEME FONKSİYONU ---
def ozetle_hybrid(metin, format_secimi):
    prompt_haritasi = {
        "maddeli": "Sana verilen haber metnindeki reklam veya detayları yok sayıp, haberin özünü Türkçe olarak maddeler halinde (📌 simgeleriyle) özetle.",
        "tek_cumle": "Sana verilen metni sadece tek ve vurucu bir cümle ile Türkçe özetle.",
        "tweet": "Sana verilen metinden ilgi çekici, bol emojili 3 maddelik bir Tweet/X flood dizisi oluştur.",
        "soru_cevap": "Sana verilen metinden en önemli 3 soruyu çıkar ve bu soruları metne göre kısaca cevapla. Format: Soru 1: ... / Cevap 1: ..."
    }

    system_prompt = prompt_haritasi.get(format_secimi, prompt_haritasi["maddeli"])

    # ⚡ 1. DENEME: GROQ (İlk Tercih - Ultra Hızlı)
    try:
        groq_client = get_groq_client()
        if groq_client:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Aşağıdaki haberi özetle:\n\n{metin}"}    
                ]
            )
            return response.choices[0].message.content
    except Exception as e:
        print(f"Groq Hatası, Gemini'ye geçiliyor: {e}")

    # 🟢 2. DENEME: GEMINI (Yedek Motor - Yüksek Bağlam)
    try:
        gemini_client = get_gemini_client()
        if gemini_client:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"{system_prompt}\n\nAşağıdaki haberi özetle:\n\n{metin}"
            )
            return response.text
    except Exception as e:
        print(f"Gemini Hatası: {e}")

    return "Maalesef özetleme servislerine şu an ulaşılamıyor. Lütfen API anahtarlarınızı kontrol edin."

@app.route('/', methods=['GET', 'POST'])
def index():
    ozet = None
    baslik = None
    gorsel_url = None
    haberler = []
    arama_kelimesi = ""

    if request.method == 'POST':
        islem_turu = request.form.get('islem_turu')
        
        # Arama Kısmı
        if islem_turu == 'ara':
            arama_kelimesi = request.form.get('query', '').strip()
            if arama_kelimesi:
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.news(keywords=arama_kelimesi, region="tr-tr", max_results=5))
                        for item in results:
                            haberler.append({
                                'title': item.get('title'),
                                'url': item.get('url'),
                                'source': item.get('source'),
                                'date': item.get('date', '')[:10]
                            })
                except Exception as e:
                    print("Arama hatası:", e)

        # Özetleme Kısmı
        elif islem_turu == 'ozetle':
            url = request.form.get('url', '').strip()
            format_secimi = request.form.get('format', 'maddeli')
            
            if url:
                baslik, gorsel_url, metin = haber_metni_cek(url)
                if metin and len(metin) > 100:
                    ozet = ozetle_hybrid(metin, format_secimi)
                else:
                    ozet = "Haber içeriği çekilemedi veya metin çok kısa."

    return render_template('index.html', ozet=ozet, baslik=baslik, gorsel_url=gorsel_url, haberler=haberler, arama_kelimesi=arama_kelimesi)

@app.route('/indir', methods=['POST'])
def indir():
    ozet_metni = request.form.get('ozet_metni', '')
    buffer = io.BytesIO()
    buffer.write(ozet_metni.encode('utf-8'))
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name="haber_ozeti.txt",
        mimetype="text/plain"
    )

if __name__ == '__main__':
    app.run(debug=True)
