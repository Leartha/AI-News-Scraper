from flask import Flask, render_template, request, send_file
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
import os
import io
from duckduckgo_search import DDGS

app = Flask(__name__)

# Gemini API Kurulumu
GENAI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)

def haber_metni_cek(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Başlık Al
        baslik = soup.find('h1')
        baslik_metni = baslik.get_text().strip() if baslik else ""

        # Görsel Al
        og_image = soup.find('meta', property='og:image')
        gorsel_url = og_image['content'] if og_image and 'content' in og_image.attrs else ""

        # Metin Paragrafları
        paragraflar = soup.find_all('p')
        haber_metni = " ".join([p.get_text().strip() for p in paragraflar if len(p.get_text().strip()) > 20])
        
        return baslik_metni, gorsel_url, haber_metni
    except Exception as e:
        return "", "", None

def ozetle_gemini(metin, format_secimi):
    if not GENAI_API_KEY:
        return "Hata: GEMINI_API_KEY tanımlanmamış."
    
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt_haritasi = {
        "maddeli": "Aşağıdaki haber metnini Türkçe olarak ana noktalarıyla madde madde (📌 simgeleriyle) özetle:\n\n",
        "tek_cumle": "Aşağıdaki haber metnini Türkçe olarak tam 1 cümlelik vurucu bir yönetici özeti haline getir:\n\n",
        "tweet": "Aşağıdaki haberi X/Twitter'da paylaşılacak tonda, ilgi çekici bir Tweet dizisi (1/3, 2/3 gibi) şeklinde yaz:\n\n",
        "soru_cevap": "Aşağıdaki haberden 3 temel soru çıkar ve bunlara metne göre kısa cevaplar ver (Soru-Cevap formatında):\n\n"
    }

    secilen_prompt = prompt_haritasi.get(format_secimi, prompt_haritasi["maddeli"])

    try:
        response = model.generate_content(secilen_prompt + metin)
        return response.text
    except Exception as e:
        return f"Özetlenirken hata oluştu: {e}"

@app.route('/', methods=['GET', 'POST'])
def index():
    ozet = None
    baslik = None
    gorsel_url = None
    haberler = []
    arama_kelimesi = ""

    if request.method == 'POST':
        islem_turu = request.form.get('islem_turu')
        
        # 1. DURUM: Arama Çubuğundan Kelime Aratıldıysa
        if islem_turu == 'ara':
            arama_kelimesi = request.form.get('query', '').strip()
            if arama_kelimesi:
                try:
                    with DDGS() as ddgs:
                        # Son güncel Türkçe haberleri arat
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

        # 2. DURUM: Arama Sonuçlarından Bir Habere "Özetle" Denildiyse VEYA Direkt Link Girildiyse
        elif islem_turu == 'ozetle':
            url = request.form.get('url', '').strip()
            format_secimi = request.form.get('format', 'maddeli')
            
            if url:
                baslik, gorsel_url, metin = haber_metni_cek(url)
                if metin and len(metin) > 100:
                    ozet = ozetle_gemini(metin, format_secimi)
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
