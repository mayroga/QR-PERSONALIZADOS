import io,os,re,base64
from flask import Flask,request,jsonify,send_file
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image,ImageDraw,ImageFont,ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=5*1024*1024
MAX_TEXT=2000
MAX_FIELD=120
MAX_LOGO_BYTES=2*1024*1024

def clean_text(value,limit=MAX_FIELD):
    value=str(value or "").strip()
    value=re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]","",value)
    return value[:limit]

def valid_color(value,fallback):
    value=str(value or "").strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}",value) else fallback

def load_font(size,bold=False):
    paths=(
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for path in paths:
        try:
            return ImageFont.truetype(path,size=size)
        except OSError:
            pass
    return ImageFont.load_default()

def fit_font(draw,text,max_width,start_size,bold=False,min_size=14):
    size=start_size
    while size>min_size:
        font=load_font(size,bold)
        if draw.textbbox((0,0),text or " ",font=font)[2]<=max_width:
            return font
        size-=2
    return load_font(min_size,bold)

def center_text(draw,text,y,font,fill,width):
    if not text:
        return y
    bbox=draw.textbbox((0,0),text,font=font)
    x=max(0,(width-(bbox[2]-bbox[0]))//2)
    draw.text((x,y),text,font=font,fill=fill)
    return y+max(28,bbox[3]-bbox[1])+10

def read_logo(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    raw=file_storage.read(MAX_LOGO_BYTES+1)
    if not raw:
        return None
    if len(raw)>MAX_LOGO_BYTES:
        raise ValueError("El logotipo debe pesar como máximo 2 MB.")
    try:
        logo=Image.open(io.BytesIO(raw))
        if logo.format not in {"PNG","JPEG","WEBP"}:
            raise ValueError("Usa un logotipo PNG, JPG o WEBP.")
        logo.load()
        logo=ImageOps.exif_transpose(logo).convert("RGBA")
        logo.thumbnail((260,260),Image.Resampling.LANCZOS)
        return logo
    except ValueError:
        raise
    except (OSError,Image.DecompressionBombError):
        raise ValueError("No se pudo leer el logotipo. Prueba con PNG, JPG o WEBP.")

def compose_design(data,logo=None):
    payload=clean_text(data.get("text"),MAX_TEXT)
    if not payload:
        raise ValueError("Escribe una URL o un texto para codificar en el QR.")
    bg=valid_color(data.get("background"),"#FFFFFF")
    fg=valid_color(data.get("foreground"),"#172033")
    accent=valid_color(data.get("accent"),"#2563EB")
    name=clean_text(data.get("name"))
    address=clean_text(data.get("address"))
    phone=clean_text(data.get("phone"))
    phrase=clean_text(data.get("phrase"))
    style=clean_text(data.get("style"),20)
    if style not in {"modern","elegant","friendly","classic"}:
        style="modern"

    qr=qrcode.QRCode(version=None,error_correction=ERROR_CORRECT_H,box_size=12,border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image=qr.make_image(fill_color=fg,back_color=bg).convert("RGB")
    qr_size=660
    qr_image=qr_image.resize((qr_size,qr_size),Image.Resampling.NEAREST)

    width=1200
    pad=72
    top_h=130 if name else 55
    logo_h=180 if logo else 0
    info_lines=[v for v in (address,phone,phrase) if v]
    height=pad+top_h+logo_h+qr_size+72+len(info_lines)*58+pad
    image=Image.new("RGB",(width,height),bg)
    draw=ImageDraw.Draw(image)

    if style=="elegant":
        draw.rectangle((0,0,width,18),fill=accent)
        draw.rectangle((pad,pad//2,width-pad,pad//2+2),fill=accent)
    elif style=="friendly":
        draw.rounded_rectangle((26,26,width-26,height-26),radius=48,outline=accent,width=8)
    elif style=="classic":
        draw.rectangle((38,38,width-38,height-38),outline=accent,width=4)
    else:
        draw.rectangle((0,0,width,20),fill=accent)

    y=pad
    if name:
        font=fit_font(draw,name,width-2*pad,58,True,24)
        y=center_text(draw,name,y,font,fg,width)
    else:
        y+=18

    if logo:
        logo_copy=logo.copy()
        logo_copy.thumbnail((180,150),Image.Resampling.LANCZOS)
        image.paste(logo_copy,((width-logo_copy.width)//2,y),logo_copy)
        y+=162

    image.paste(qr_image,((width-qr_size)//2,y))
    y+=qr_size+38

    for text,color,bold in (
        (address,fg,False),
        (phone,fg,False),
        (phrase,accent,style!="classic")
    ):
        if text:
            font=fit_font(draw,text,width-2*pad,34 if bold else 31,bold,18)
            y=center_text(draw,text,y+(6 if text==phrase else 0),font,color,width)

    return image,payload

@app.get("/")
def index():
    return send_file("index.html")

@app.post("/generate_qr")
def generate_qr():
    try:
        data=request.get_json(silent=True) or {} if request.is_json else request.form.to_dict()
        logo=None if request.is_json else read_logo(request.files.get("logo"))
        image,payload=compose_design(data,logo)
        buffer=io.BytesIO()
        image.save(buffer,format="PNG",optimize=True)
        encoded=base64.b64encode(buffer.getvalue()).decode("ascii")
        return jsonify({"image":"data:image/png;base64,"+encoded,"payload":payload})
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error generando QR")
        return jsonify({"error":"No se pudo generar el diseño. Revisa los datos e inténtalo otra vez."}),500

@app.post("/download_png")
def download_png():
    try:
        image,_=compose_design(request.form.to_dict(),read_logo(request.files.get("logo")))
        buffer=io.BytesIO()
        image.save(buffer,format="PNG",optimize=True)
        buffer.seek(0)
        return send_file(buffer,mimetype="image/png",as_attachment=True,download_name="qr-personalizado.png")
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PNG")
        return jsonify({"error":"No se pudo crear el PNG."}),500

@app.post("/download_pdf")
def download_pdf():
    try:
        image,_=compose_design(request.form.to_dict(),read_logo(request.files.get("logo")))
        png=io.BytesIO()
        image.save(png,format="PNG",optimize=True)
        png.seek(0)
        output=io.BytesIO()
        page_w,page_h=A4
        margin=28
        pdf=canvas.Canvas(output,pagesize=A4)
        iw,ih=image.size
        scale=min((page_w-2*margin)/iw,(page_h-2*margin)/ih)
        draw_w,draw_h=iw*scale,ih*scale
        pdf.drawImage(ImageReader(png),(page_w-draw_w)/2,(page_h-draw_h)/2,width=draw_w,height=draw_h,preserveAspectRatio=True,mask="auto")
        pdf.showPage()
        pdf.save()
        output.seek(0)
        return send_file(output,mimetype="application/pdf",as_attachment=True,download_name="qr-personalizado.pdf")
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PDF")
        return jsonify({"error":"No se pudo crear el PDF."}),500

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error":"La petición es demasiado grande. Reduce el logotipo a menos de 2 MB."}),413

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")),debug=False)
