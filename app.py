import os
import json
import io
import re
import urllib.parse
from flask import Flask, render_template, request, send_file, jsonify
import requests
from bs4 import BeautifulSoup
from groq import Groq
import google.generativeai as genai
from gtts import gTTS

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

# --- HABER ARAMA (Bing News RSS) ---
def haber_ara(kelime):
    haberler = []
    if not kelime:
        return haberler
        
    try:
        query = urllib.parse.quote(kelime)
        rss_url = f"https://www.bing.com/news/search?q={query}&format=rss&cc=tr"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=5)
        soup = BeautifulSoup(response.content, 'html.parser')
            
        items = soup.find_all('item')[:5]

        for item in items:
            title_tag = item.find('title')
            link_tag = item.find('link')
            pub_date_tag = item.find('pubdate') or item.find('pubDate')

            title = title_tag.get_text().strip() if title_tag else 'Başlık Yok'
            
            link = ""
            if link_tag:
                link = link_tag.get_text().strip() if link_tag.get_text() else ""

            pub_date = pub_date_tag.get_text()[:16] if pub_date_tag else ''

            source = 'Haber Kaynağı'
            if link:
                domain_match = re.search(r'https?://(?:www\.)?([^/]+)', link)
                if domain_match:
                    source = domain_match.group(1).capitalize()

            if title and link and link.startswith('http'):
                haberler.append({
                    'title': title,
                    'url': link,
                    'source': source,
                    'date': pub_date
                })
    except Exception as e:
        print("Haber arama hatası:", e)
    return haberler

# --- HABER METNİ ÇEKİCİ ---
def haber_metni_cek(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.google.com/'
        }
        
        response = requests.get(url, headers=headers, timeout=7, allow_redirects=True)
        soup = BeautifulSoup(response.text, 'html.parser')

        baslik = soup.find('h1')
        baslik_metni = baslik.get_text().strip() if baslik else ""

        og_image = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'twitter:image'})
        gorsel_url = og_image['content'] if og_image and 'content' in og_image.attrs else ""

        for cop in soup(["script", "style", "header", "footer", "nav", "aside", "form", "iframe", "noscript"]):
            cop.decompose()

        paragraflar = soup.find_all('p')
        metin_parcalari = [p.get_text().strip() for p in paragraflar if len(p.get_text().strip()) > 25]
        haber_metni = " ".join(metin_parcalari)

        if len(haber_metni) < 100:
            article = soup.find('article') or soup.find('div', class_=re.compile(r'content|article|detail|news|post-body', re.I))
            if article:
                haber_metni = article.get_text().strip()

        return baslik_metni, gorsel_url, haber_metni
    except Exception as e:
        print("Haber çekme hatası:", e)
        return "", "", None

# --- HYBRID ÖZETLEME ---
def ozetle_hybrid(metin, format_secimi, hedef_dil="tr"):
    dil_haritasi = {"tr": "Türkçe", "en": "İngilizce", "de": "Almanca", "es": "İspanyolca", "fr": "Fransızca", "it": "İtalyanca"}
    dil_adi = dil_haritasi.get(hedef_dil, "Türkçe")

    prompt_haritasi = {
        "maddeli": f"Haber metninin özünü {dil_adi} dilinde maddeler halinde (📌 simgeleriyle) özetle.",
        "tek_cumle": f"Haber metnini {dil_adi} dilinde sadece tek ve vurucu bir cümle ile özetle.",
        "tweet": f"Haber metninden {dil_adi} dilinde ilgi çekici 3 maddelik bir Tweet/X flood dizisi oluştur.",
        "soru_cevap": f"Haber metninden en önemli 3 soruyu çıkar ve {dil_adi} dilinde cevapla (Soru 1: ... / Cevap 1: ...)."
    }

    secilen_format = prompt_haritasi.get(format_secimi, prompt_haritasi["maddeli"])

    system_prompt = f"""Sen uzman bir haber analistisin.
Cevabını SADECE geçerli bir JSON formatında ver.
JSON Yapısı:
{{
  "duygu": "Pozitif" veya "Negatif" veya "Nötr",
  "ozet": "Özet metni ({secilen_format})"
}}"""

    raw_response = None

    try:
        groq_client = get_groq_client()
        if groq_client:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Haber:\n{metin}"}
                ],
                response_format={"type": "json_object"},
                timeout=8
            )
            raw_response = response.choices[0].message.content
    except Exception as e:
        print("Groq hatası: ", e)

    if not raw_response:
        try:
            if setup_gemini():
                model = genai.GenerativeModel('gemini-1.5-flash')
                res = model.generate_content(f"{system_prompt}\n\nHaber:\n{metin}")
                raw_response = res.text
        except Exception as e:
            print("Gemini Hatası: ", e)

    if raw_response:
        try:
            cleaned = re.sub(r'```json|```', '', raw_response).strip()
            data = json.loads(cleaned)
            return data.get("ozet", "Özet oluşturulamadı."), data.get("duygu", "Nötr")
        except Exception:
            return raw_response, "Nötr"

    return "Özet oluşturulamadı. API Keylerinizi kontrol edin.", "Nötr"


def habere_soru_sor(haber_metni, soru):
    system_prompt = """Sen uzman bir haber asistanısın. 
Görevin, kullanıcının sorusunu SADECE sana verilen haber metnindeki bilgilere dayanarak cevaplamaktır.
Eğer sorunun cevabı haber metninde YOKSA, dürüstçe 'Bu sorunun cevabı verilen haber metninde yer almıyor.' de ve tahmin yürütme.
Cevabın net, kısa ve anlaşılır olsun."""

    user_prompt = f"""HABER METNİ:
{haber_metni}

KULLANICININ SORUSU:
{soru}"""

    # 1. Deneme: Groq
    try:
        groq_client = get_groq_client()
        if groq_client:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                timeout=8
            )
            return response.choices[0].message.content
    except Exception as e:
        print("Groq Soru-Cevap Hatası:", e)

    # 2. Deneme: Gemini (Yedek)
    try:
        if setup_gemini():
            model = genai.GenerativeModel('gemini-1.5-flash')
            res = model.generate_content(f"{system_prompt}\n\n{user_prompt}")
            return res.text
    except Exception as e:
        print("Gemini Soru-Cevap Hatası:", e)

    return "Cevap üretilirken bir hata oluştu. Lütfen daha sonra tekrar deneyin."


@app.route('/', methods=['GET', 'POST'])
def index():
    ozet = None
    duygu = None
    baslik = None
    gorsel_url = None
    haber_metni = None
    haberler = []
    
    arama_kelimesi = request.args.get('query', '').strip()
    
    if arama_kelimesi:
        haberler = haber_ara(arama_kelimesi)

    if request.method == 'POST':
        islem_turu = request.form.get('islem_turu')
        
        if islem_turu == 'ozetle':
            url = request.form.get('url', '').strip()
            format_secimi = request.form.get('format', 'maddeli')
            hedef_dil = request.form.get('dil', 'tr')
            
            if url:
                baslik, gorsel_url, metin = haber_metni_cek(url)
                if metin and len(metin) > 50:
                    haber_metni = metin
                    ozet, duygu = ozetle_hybrid(metin, format_secimi, hedef_dil)
                else:
                    ozet = "Haber içeriği çekilemedi veya metin çok kısa. Lütfen başka bir haber linki deneyin."

    return render_template('index.html', ozet=ozet, duygu=duygu, baslik=baslik, gorsel_url=gorsel_url, haber_metni=haber_metni, haberler=haberler, arama_kelimesi=arama_kelimesi)


# 🎙️ VERCEL UYUMLU GERÇEKÇİ KADIN SESİ (RAM ÜZERİNDEN STREAM)
@app.route('/seslendir', methods=['POST'])
def seslendir_api():
    try:
        data = request.get_json(silent=True) or {}
        metin = (data.get("metin") or "").strip()

        # Emojileri temizle
        metin_temiz = re.sub(r'[^\w\s,.\?!áéíóúâêîôûàèìòùäëïöüÇçĞğİıÖöŞşÜü-]', '', metin)

        if not metin_temiz.strip():
            return jsonify({"error": "Metin bulunamadı"}), 400

        # Google'ın Doğal Türkçe Kadın Sesi ile MP3 üret (RAM belleğinde)
        tts = gTTS(text=metin_temiz, lang='tr', slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)

        return send_file(
            fp,
            mimetype="audio/mpeg",
            as_attachment=False
        )
    except Exception as e:
        print("Ses hatası:", e)
        return jsonify({"error": str(e)}), 500


@app.route('/soru-sor', methods=['POST'])
def soru_sor_api():
    try:
        data = request.get_json(silent=True) or {}
        haber_metni = (data.get("haber_metni") or "").strip()
        soru = (data.get("soru") or "").strip()

        if not haber_metni:
            return jsonify({"cevap": "Haber metni bulunamadı. Lütfen önce bir haberi özetleyin."}), 400

        if not soru:
            return jsonify({"cevap": "Lütfen geçerli bir soru yazın."}), 400

        cevap = habere_soru_sor(haber_metni, soru)
        return jsonify({"cevap": cevap})

    except Exception as e:
        print("Soru API Hatası", e)
        return jsonify({"cevap": "Sunucu hatası oluştu."}), 500


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
