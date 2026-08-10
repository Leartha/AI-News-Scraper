import os
import json
import io
import re
import urllib.parse
from flask import Flask, render_template, request, send_file
import requests
from bs4 import BeautifulSoup
from groq import Groq
import google.generativeai as genai

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

def setup_gemini():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        try:
            with open("api_keys.json", "r", encoding="utf-8") as f:
                key = json.load(f).get("gemini_api_key")
        except Exception:
            pass
    if key:
        genai.configure(api_key=key)
        return True
    return False

# --- HABER ARAMA (Gerçek Link Ayıklayıcı ile) ---
def haber_ara(kelime):
    haberler = []
    if not kelime:
        return haberler
        
    try:
        query = urllib.parse.quote(kelime)
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=tr&gl=TR&ceid=TR:tr"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.content, 'html.parser')
            
        items = soup.find_all('item')[:5]

        for item in items:
            title_tag = item.find('title')
            source_tag = item.find('source')
            pub_date_tag = item.find('pubdate') or item.find('pubDate')
            description_tag = item.find('description')

            title = title_tag.get_text() if title_tag else 'Başlık Yok'
            source = source_tag.get_text() if source_tag else 'Haber Kaynağı'
            pub_date = pub_date_tag.get_text()[:16] if pub_date_tag else ''

            # Google RSS linki yerine description içindeki GERÇEK site linkini ayıkla
            gercek_link = ""
            if description_tag:
                desc_soup = BeautifulSoup(description_tag.get_text(), 'html.parser')
                a_tag = desc_soup.find('a')
                if a_tag and a_tag.get('href'):
                    gercek_link = a_tag.get('href')

            # Eğer description'dan çıkmazsa varsayılan linki al
            if not gercek_link:
                link_tag = item.find('link')
                if link_tag:
                    gercek_link = link_tag.get_text() if link_tag.get_text() else link_tag.next_sibling

            if title and gercek_link:
                haberler.append({
                    'title': title,
                    'url': str(gercek_link).strip(),
                    'source': source,
                    'date': pub_date
                })
    except Exception as e:
        print("Haber arama hatası:", e)
    return haberler

# --- HABER METNİ ÇEKİCİ (Gelişmiş Bot Koruma Aşma) ---
def haber_metni_cek(url):
    try:
        # Gerçek bir tarayıcı gibi davranıyoruz
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.google.com/'
        }
        
        # Yönlendirmeleri takip et
        response = requests.get(url, headers=headers, timeout=6, allow_redirects=True)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Başlığı Çek
        baslik = soup.find('h1')
        baslik_metni = baslik.get_text().strip() if baslik else ""

        # Görseli Çek
        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        gorsel_url = og_image['content'] if og_image and 'content' in og_image.attrs else ""

        # Çöp etiketleri temizle
        for cop in soup(["script", "style", "header", "footer", "nav", "aside", "form", "iframe", "noscript"]):
            cop.decompose()

        # Metinleri topla
        paragraflar = soup.find_all('p')
        metin_parcalari = [p.get_text().strip() for p in paragraflar if len(p.get_text().strip()) > 20]
        haber_metni = " ".join(metin_parcalari)

        # Eğer paragraflardan metin çıkmazsa haber / article tag'ine bak
        if len(haber_metni) < 50:
            article = soup.find('article') or soup.find('div', class_=re.compile(r'content|article|detail|news', re.I))
            if article:
                haber_metni = article.get_text().strip()

        return baslik_metni, gorsel_url, haber_metni
    except Exception as e:
        print("Haber çekme hatası:", e)
        return "", "", None

# --- HYBRID ÖZETLEME ---
def ozetle_hybrid(metin, format_secimi):
    prompt_haritasi = {
        "maddeli": "Sana verilen haber metnindeki reklam veya detayları yok sayıp, haberin özünü Türkçe olarak maddeler halinde (📌 simgeleriyle) özetle.",
        "tek_cumle": "Sana verilen metni sadece tek ve vurucu bir cümle ile Türkçe özetle.",
        "tweet": "Sana verilen metinden ilgi çekici, bol emojili 3 maddelik bir Tweet/X flood dizisi oluştur.",
        "soru_cevap": "Sana verilen metinden en önemli 3 soruyu çıkar ve bu soruları metne göre kısaca cevapla. Format: Soru 1: ... / Cevap 1: ..."
    }

    system_prompt = prompt_haritasi.get(format_secimi, prompt_haritasi["maddeli"])

    # 1. Groq Denemesi
    try:
        groq_client = get_groq_client()
        if groq_client:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Aşağıdaki haberi özetle:\n\n{metin}"}    
                ],
                timeout=8
            )
            return response.choices[0].message.content
    except Exception as e:
        print("Groq hatası:", e)

    # 2. Gemini Denemesi (Yedek)
    try:
        if setup_gemini():
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(f"{system_prompt}\n\nAşağıdaki haberi özetle:\n\n{metin}")
            return response.text
    except Exception as e:
        print("Gemini hatası:", e)

    return "Özet oluşturulamadı. Lütfen API anahtarlarınızı (GROQ_API_KEY veya GEMINI_API_KEY) Vercel ayarlarından kontrol edin."

@app.route('/', methods=['GET', 'POST'])
def index():
    ozet = None
    baslik = None
    gorsel_url = None
    haberler = []
    
    arama_kelimesi = request.args.get('query', '').strip()
    
    if arama_kelimesi:
        haberler = haber_ara(arama_kelimesi)

    if request.method == 'POST':
        islem_turu = request.form.get('islem_turu')
        
        if islem_turu == 'ozetle':
            url = request.form.get('url', '').strip()
            format_secimi = request.form.get('format', 'maddeli')
            
            if url:
                baslik, gorsel_url, metin = haber_metni_cek(url)
                if metin and len(metin) > 50:
                    ozet = ozetle_hybrid(metin, format_secimi)
                else:
                    ozet = "Haber içeriği çekilemedi veya metin çok kısa. Lütfen başka bir haber linki deneyin."

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

app = app

if __name__ == '__main__':
    app.run(debug=True)
