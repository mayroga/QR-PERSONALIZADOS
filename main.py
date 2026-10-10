
from __future__ import annotations
import os,io,sqlite3,uuid,calendar
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
from flask import Flask,request,jsonify,send_file,send_from_directory,redirect
from PIL import Image,ImageDraw,ImageFont,ImageOps
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=4*1024*1024
DB_PATH=os.environ.get("DB_PATH","qr_data.db")
PUBLIC_BASE_URL=os.environ.get("PUBLIC_BASE_URL","").rstrip("/")
DURATIONS={
"1d":("1 día",1,"days"),
"1m":("1 mes",1,"months"),
"6m":("6 meses",6,"months"),
"1y":("1 año",1,"years"),
"2y":("2 años",2,"years"),
"3y":("3 años",3,"years"),
"4y":("4 años",4,"years"),
"5y":("5 años",5,"years"),
"life":("De por vida",None,None)
}
def now_utc():
    return datetime.now(timezone.utc)
def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()
def parse_dt(value):
    return datetime.fromisoformat(value) if value else None
def add_months(dt,months):
    month=dt.month-1+months
    year=dt.year+month//12
    month=month%12+1
    day=min(dt.day,calendar.monthrange(year,month)[1])
    return dt.replace(year=year,month=month,day=day)
def expiration_for(key):
    if key not in DURATIONS:
        raise ValueError("Selecciona una duración válida.")
    label,amount,unit=DURATIONS[key]
    if key=="life":
        return label,None
    now=now_utc()
    if unit=="days":
        end=now+timedelta(days=amount)
    elif unit=="months":
        end=add_months(now,amount)
    else:
        end=add_months(now,amount*12)
    return label,iso(end)
def connect():
    db=sqlite3.connect(DB_PATH,timeout=20)
    db.row_factory=sqlite3.Row
    return db
def init_db():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS qr_codes(
        id TEXT PRIMARY KEY,target_url TEXT NOT NULL,name TEXT NOT NULL,
        address TEXT NOT NULL,phone TEXT NOT NULL,phrase TEXT NOT NULL,
        style TEXT NOT NULL,background TEXT NOT NULL,foreground TEXT NOT NULL,
        accent TEXT NOT NULL,duration_key TEXT NOT NULL,duration_label TEXT NOT NULL,
        expires_at TEXT,created_at TEXT NOT NULL,image_png BLOB NOT NULL)""")
init_db()
def valid_url(value):
    try:
        p=urlparse((value or "").strip())
        return p.scheme in ("http","https") and bool(p.netloc) and "." in p.netloc
    except Exception:
        return False
def get_font(size,bold=False):
    paths=[
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path,size)
        except Exception:
            pass
    return ImageFont.load_default()
def hex_color(value,default):
    value=(value or "").strip()
    if len(value)==7 and value.startswith("#"):
        try:
            int(value[1:],16)
            return value
        except ValueError:
            pass
    return default
def read_logo(file):
    if not file or not file.filename:
        return None
    raw=file.read()
    if len(raw)>2*1024*1024:
        raise ValueError("El logotipo no puede superar 2 MB.")
    try:
        im=Image.open(io.BytesIO(raw))
        if im.format not in ("PNG","JPEG","WEBP"):
            raise ValueError("Usa un logotipo PNG, JPG o WEBP.")
        im=ImageOps.exif_transpose(im).convert("RGBA")
        im.thumbnail((220,220))
        return im
    except ValueError:
        raise
    except Exception:
        raise ValueError("No se pudo leer el logotipo.")
def compose_design(data,qr_image,logo=None):
    bg=hex_color(data.get("background"),"#FFFFFF")
    fg=hex_color(data.get("foreground"),"#111111")
    accent=hex_color(data.get("accent"),"#1677FF")
    style=data.get("style","modern")
    name=(data.get("name") or "").strip()[:100]
    address=(data.get("address") or "").strip()[:200]
    phone=(data.get("phone") or "").strip()[:60]
    phrase=(data.get("phrase") or "").strip()[:180]
    W,H=1000,1320
    im=Image.new("RGB",(W,H),bg)
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((35,35,W-35,H-35),radius=35,outline=accent,width=8)
    if style=="elegant":
        d.line((120,105,W-120,105),fill=accent,width=4)
    if logo:
        logo.thumbnail((180,180))
        im.paste(logo,(W//2-logo.width//2,75),logo)
        top=285
    else:
        top=105
    title_font=get_font(48,True)
    sub_font=get_font(28)
    small_font=get_font(25)
    if name:
        box=d.textbbox((0,0),name,font=title_font)
        d.text(((W-(box[2]-box[0]))//2,top),name,font=title_font,fill=fg)
        top+=85
    labels={"modern":"ESCANEA AQUÍ","elegant":"Accede fácilmente","friendly":"¡Escanéame!","classic":"CÓDIGO QR"}
    label=labels.get(style,"ESCANEA AQUÍ")
    box=d.textbbox((0,0),label,font=sub_font)
    d.text(((W-(box[2]-box[0]))//2,top),label,font=sub_font,fill=accent)
    q=qr_image.convert("RGB")
    q.thumbnail((650,650))
    qx=(W-q.width)//2
    qy=top+60
    d.rounded_rectangle((qx-20,qy-20,qx+q.width+20,qy+q.height+20),radius=20,fill="#FFFFFF",outline=accent,width=4)
    im.paste(q,(qx,qy))
    top=qy+q.height+65
    for value,font in ((address,sub_font),(phone,sub_font),(phrase,small_font)):
        if not value:
            continue
        words=value.split()
        lines=[]
        line=""
        for word in words:
            test=(line+" "+word).strip()
            if d.textbbox((0,0),test,font=font)[2] > W-150 and line:
                lines.append(line)
                line=word
            else:
                line=test
        if line:
            lines.append(line)
        for line in lines[:3]:
            box=d.textbbox((0,0),line,font=font)
            d.text(((W-(box[2]-box[0]))//2,top),line,font=font,fill=fg)
            top+=box[3]-box[1]+12
        top+=8
    return im
def get_base_url():
    return PUBLIC_BASE_URL or request.url_root.rstrip("/")
@app.get("/")
def home():
    return send_from_directory(".", "index.html")
@app.post("/generate_qr")
def generate_qr():
    try:
        data=request.form
        target=(data.get("text") or "").strip()
        if not valid_url(target):
            return jsonify(ok=False,error="Para que la duración funcione, introduce una dirección web completa, por ejemplo https://tusitio.com."),400
        key=data.get("duration","1m")
        label,expires=expiration_for(key)
        logo=read_logo(request.files.get("logo"))
        code=uuid.uuid4().hex[:16]
        base=get_base_url()
        dynamic_url=f"{base}/q/{code}"
        qr=qrcode.QRCode(version=None,error_correction=ERROR_CORRECT_H,box_size=14,border=5)
        qr.add_data(dynamic_url)
        qr.make(fit=True)
        qr_image=qr.make_image(fill_color=hex_color(data.get("foreground"),"#111111"),back_color="#FFFFFF")
        design=compose_design(data,qr_image,logo)
        out=io.BytesIO()
        design.save(out,format="PNG",optimize=True)
        png=out.getvalue()
        with connect() as db:
            db.execute("""INSERT INTO qr_codes
            (id,target_url,name,address,phone,phrase,style,background,foreground,accent,
            duration_key,duration_label,expires_at,created_at,image_png)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code,target,(data.get("name") or "").strip()[:100],
            (data.get("address") or "").strip()[:200],
            (data.get("phone") or "").strip()[:60],
            (data.get("phrase") or "").strip()[:180],
            data.get("style","modern"),hex_color(data.get("background"),"#FFFFFF"),
            hex_color(data.get("foreground"),"#111111"),
            hex_color(data.get("accent"),"#1677FF"),key,label,expires,iso(now_utc()),png))
        from base64 import b64encode
        return jsonify(ok=True,id=code,preview="data:image/png;base64,"+b64encode(png).decode(),
        duration=label,expires_at=expires,qr_url=dynamic_url,
        message=("Este QR no caduca." if expires is None else "Válido hasta "+expires))
    except ValueError as e:
        return jsonify(ok=False,error=str(e)),400
    except Exception:
        app.logger.exception("Error generando QR")
        return jsonify(ok=False,error="No se pudo crear el QR. Revisa los datos e inténtalo de nuevo."),500
@app.get("/q/<code>")
def open_qr(code):
    with connect() as db:
        row=db.execute("SELECT target_url,expires_at FROM qr_codes WHERE id=?",(code,)).fetchone()
    if not row:
        return "<h2>Este código QR no existe o ya no está disponible.</h2>",404
    expiry=parse_dt(row["expires_at"])
    if expiry and now_utc()>=expiry.astimezone(timezone.utc):
        return "<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>QR vencido</title><body style='font-family:Arial;text-align:center;padding:40px'><h1>Este código QR ha vencido</h1><p>El período de uso terminó. Contacta con quien te lo proporcionó.</p></body></html>",410
    return redirect(row["target_url"],code=302)
def get_image(code):
    with connect() as db:
        row=db.execute("SELECT image_png,name FROM qr_codes WHERE id=?",(code,)).fetchone()
    return row
@app.get("/download_png/<code>")
def download_png(code):
    row=get_image(code)
    if not row:
        return "QR no encontrado.",404
    return send_file(io.BytesIO(row["image_png"]),mimetype="image/png",
    as_attachment=True,download_name=f"qr_{code}.png")
@app.get("/download_pdf/<code>")
def download_pdf(code):
    row=get_image(code)
    if not row:
        return "QR no encontrado.",404
    image=Image.open(io.BytesIO(row["image_png"])).convert("RGB")
    png=io.BytesIO()
    image.save(png,format="PNG")
    png.seek(0)
    pdf=io.BytesIO()
    c=canvas.Canvas(pdf,pagesize=letter)
    pw,ph=letter
    margin=30
    maxw,maxh=pw-2*margin,ph-2*margin
    ratio=min(maxw/image.width,maxh/image.height)
    w,h=image.width*ratio,image.height*ratio
    c.drawImage(ImageReader(png),(pw-w)/2,(ph-h)/2,width=w,height=h, preserveAspectRatio=True,mask="auto")
    c.save()
    pdf.seek(0)
    return send_file(pdf,mimetype="application/pdf",as_attachment=True,
    download_name=f"qr_{code}.pdf")
@app.get("/health")
def health():
    return jsonify(ok=True,service="QR dinámicos con vencimiento")
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")),debug=False)
