import io,os,re,base64
from flask import Flask,request,jsonify,send_file
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image,ImageDraw,ImageFont,ImageOps
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=15*1024*1024
MAX_TEXT=2000
MAX_FIELD=120
MAX_LOGO_BYTES=2*1024*1024
MAX_PHOTO_BYTES=8*1024*1024
WIDTH=1800
PAD=90
QR_SIZE=1000

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

def fit_font(draw,text,max_width,start_size,bold=False,min_size=18):
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
    return y+max(30,bbox[3]-bbox[1])+14

def read_image(file_storage,max_bytes,label):
    if not file_storage or not file_storage.filename:
        return None
    raw=file_storage.read(max_bytes+1)
    if not raw:
        return None
    if len(raw)>max_bytes:
        raise ValueError(f"{label}: el archivo supera el límite permitido.")
    try:
        img=Image.open(io.BytesIO(raw))
        if img.format not in {"PNG","JPEG","WEBP"}:
            raise ValueError(f"{label}: utiliza una imagen PNG, JPG o WEBP.")
        if img.width*img.height>40000000:
            raise ValueError(f"{label}: la imagen tiene demasiados píxeles.")
        img.load()
        return ImageOps.exif_transpose(img).convert("RGBA")
    except ValueError:
        raise
    except (OSError,Image.DecompressionBombError):
        raise ValueError(f"No se pudo leer {label}. Prueba con PNG, JPG o WEBP.")

def read_logo(file_storage):
    return read_image(file_storage,MAX_LOGO_BYTES,"El logotipo")

def read_photo(file_storage):
    return read_image(file_storage,MAX_PHOTO_BYTES,"La fotografía")

def compose_design(data,logo=None,photo=None):
    payload=clean_text(data.get("text"),MAX_TEXT)
    if not payload:
        raise ValueError("Escribe una dirección web o un texto para el QR.")

    bg=valid_color(data.get("background"),"#FFFFFF")
    fg=valid_color(data.get("foreground"),"#172033")
    accent=valid_color(data.get("accent"),"#2563EB")
    name=clean_text(data.get("name"))
    address=clean_text(data.get("address"))
    phone=clean_text(data.get("phone"))
    phrase=clean_text(data.get("phrase"))
    style=clean_text(data.get("style"),20)
    photo_position=clean_text(data.get("photo_position"),10).lower()

    if style not in {"modern","elegant","friendly","classic"}:
        style="modern"
    if photo_position not in {"top","bottom"}:
        photo_position="top"

    qr=qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=20,
        border=4
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image=qr.make_image(fill_color=fg,back_color=bg).convert("RGB")
    qr_image=qr_image.resize((QR_SIZE,QR_SIZE),Image.Resampling.NEAREST)

    photo_display=None
    if photo:
        photo_display=ImageOps.contain(
            photo.copy(),(WIDTH-2*PAD-40,1050),
            method=Image.Resampling.LANCZOS
        )

    photo_block=(photo_display.height+40) if photo_display else 0
    details=[v for v in (address,phone,phrase) if v]
    title_space=100 if name else 30
    logo_space=180 if logo else 0
    top_photo_space=photo_block if photo_position=="top" else 0
    bottom_photo_space=photo_block if photo_position=="bottom" else 0
    height=(
        2*PAD+title_space+logo_space+top_photo_space+40+
        QR_SIZE+55+bottom_photo_space+len(details)*78+30
    )
    image=Image.new("RGB",(WIDTH,height),bg)
    draw=ImageDraw.Draw(image)

    if style=="elegant":
        draw.rectangle((0,0,WIDTH,20),fill=accent)
        draw.rectangle((PAD,PAD//2,WIDTH-PAD,PAD//2+3),fill=accent)
    elif style=="friendly":
        draw.rounded_rectangle((26,26,WIDTH-26,height-26),radius=48,outline=accent,width=8)
    elif style=="classic":
        draw.rectangle((38,38,WIDTH-38,height-38),outline=accent,width=4)
    else:
        draw.rectangle((0,0,WIDTH,20),fill=accent)

    y=PAD
    if name:
        font=fit_font(draw,name,WIDTH-2*PAD,58,True,24)
        y=center_text(draw,name,y,font,fg,WIDTH)
    else:
        y+=18

    if logo:
        logo_copy=ImageOps.contain(logo.copy(),(260,150),method=Image.Resampling.LANCZOS)
        lx=(WIDTH-logo_copy.width)//2
        ly=y+5
        image.paste(logo_copy,(lx,ly),logo_copy)
        y+=180

    def place_photo(current_y):
        if not photo_display:
            return current_y
        pw,ph=photo_display.size
        px=(WIDTH-pw)//2
        py=current_y+10
        draw.rectangle((px-12,py-12,px+pw+12,py+ph+12),outline=accent,width=5)
        image.paste(photo_display,(px,py),photo_display)
        return py+ph+30

    if photo_position=="top" and photo_display:
        y=place_photo(y)

    y+=35
    image.paste(qr_image,((WIDTH-QR_SIZE)//2,y))
    y+=QR_SIZE+55

    if photo_position=="bottom" and photo_display:
        y=place_photo(y)

    for text,color,bold in (
        (address,fg,False),
        (phone,fg,False),
        (phrase,accent,style!="classic")
    ):
        if text:
            font=fit_font(draw,text,WIDTH-2*PAD,36,bold,20)
            y=center_text(draw,text,y,font,color,WIDTH)

    return image,payload

def make_png(image):
    output=io.BytesIO()
    image.save(output,format="PNG",optimize=True,dpi=(300,300))
    output.seek(0)
    return output

def make_pdf(image):
    output=io.BytesIO()
    png=make_png(image)
    iw,ih=image.size
    dpi=180
    page_w=iw*72/dpi
    page_h=ih*72/dpi
    pdf=canvas.Canvas(output,pagesize=(page_w,page_h),pageCompression=1)
    pdf.drawImage(
        ImageReader(png),0,0,
        width=page_w,height=page_h,
        preserveAspectRatio=True,mask="auto"
    )
    pdf.showPage()
    pdf.save()
    output.seek(0)
    return output

def request_design():
    data=request.form.to_dict()
    logo=read_logo(request.files.get("logo"))
    photo=read_photo(request.files.get("photo"))
    return compose_design(data,logo,photo)

@app.get("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__),"index.html"))

@app.post("/generate_qr")
def generate_qr():
    try:
        if request.is_json:
            data=request.get_json(silent=True) or {}
            image,payload=compose_design(data)
        else:
            image,payload=request_design()
        encoded=base64.b64encode(make_png(image).getvalue()).decode("ascii")
        return jsonify({"image":"data:image/png;base64,"+encoded,"payload":payload})
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error generando QR")
        return jsonify({"error":"No se pudo generar el diseño. Revisa los datos y las imágenes."}),500

@app.post("/download_png")
def download_png():
    try:
        image,_=request_design()
        return send_file(
            make_png(image),mimetype="image/png",
            as_attachment=True,download_name="qr-personalizado.png"
        )
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PNG")
        return jsonify({"error":"No se pudo crear el PNG."}),500

@app.post("/download_pdf")
def download_pdf():
    try:
        image,_=request_design()
        return send_file(
            make_pdf(image),mimetype="application/pdf",
            as_attachment=True,download_name="qr-personalizado.pdf"
        )
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PDF")
        return jsonify({"error":"No se pudo crear el PDF."}),500

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error":"Los archivos enviados superan el límite total de 15 MB. Reduce el tamaño de las imágenes."}),413

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")),debug=False)
