import io,os,re,base64,json,urllib.request,urllib.error
from flask import Flask,request,jsonify,send_file
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image,ImageDraw,ImageFont,ImageOps,ImageFile
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

app=Flask(__name__)
app.config["MAX_CONTENT_LENGTH"]=12*1024*1024
Image.MAX_IMAGE_PIXELS=30000000
ImageFile.LOAD_TRUNCATED_IMAGES=False
MAX_TEXT=2000
MAX_FIELD=160
MAX_LOGO_BYTES=2*1024*1024
MAX_PHOTO_BYTES=8*1024*1024
POSTER_WIDTH=1800
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
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for path in paths:
        try:
            return ImageFont.truetype(path,size=size)
        except OSError:
            pass
    return ImageFont.load_default()

def fit_font(draw,text,max_width,start_size,bold=False,min_size=18):
    for size in range(start_size,min_size-1,-2):
        font=load_font(size,bold)
        if draw.textbbox((0,0),text or " ",font=font)[2]<=max_width:
            return font
    return load_font(min_size,bold)

def center_text(draw,text,y,font,fill,width):
    if not text:
        return y
    box=draw.textbbox((0,0),text,font=font)
    x=max(0,(width-(box[2]-box[0]))//2)
    draw.text((x,y),text,font=font,fill=fill)
    return y+max(30,box[3]-box[1])+18

def read_image(file_storage,max_bytes,label):
    if not file_storage or not file_storage.filename:
        return None
    raw=file_storage.read(max_bytes+1)
    if not raw:
        return None
    if len(raw)>max_bytes:
        raise ValueError(f"{label}: el archivo supera el tamaño permitido.")
    return decode_image(raw,label)

def decode_image(raw,label="imagen"):
    try:
        im=Image.open(io.BytesIO(raw))
        if im.format not in {"PNG","JPEG","WEBP"}:
            raise ValueError("Usa imágenes PNG, JPG o WEBP.")
        im.load()
        im=ImageOps.exif_transpose(im).convert("RGBA")
        return im
    except ValueError:
        raise
    except Exception:
        raise ValueError(f"No se pudo abrir la {label}. Prueba con PNG, JPG o WEBP.")

def read_figure_data(value):
    value=str(value or "")
    if not value.startswith("data:image/") or "," not in value:
        raise ValueError("Primero genera la figura y espera a que aparezca la vista previa.")
    try:
        raw=base64.b64decode(value.split(",",1)[1],validate=True)
        if len(raw)>10*1024*1024:
            raise ValueError("La ilustración generada es demasiado grande.")
        return decode_image(raw,"ilustración")
    except ValueError:
        raise
    except Exception:
        raise ValueError("No se pudo leer la ilustración generada.")

def generate_ai_figure(prompt,style,background,accent):
    api_key=os.environ.get("GEMINI_API_KEY","").strip()
    if not api_key:
        raise ValueError("El modo figuras necesita configurar GEMINI_API_KEY en Render.")
    description=(
        "Create one polished, high-quality standalone illustration based on this request: "
        +prompt+
        ". Style: "+style+
        ". Background color preference: "+background+
        ". Accent color preference: "+accent+
        ". Center the main subject, use clean composition, crisp edges, clear visible details, "
        "professional advertising artwork, no QR code, no mockup, no watermark, no extra text. "
        "Keep the subject fully inside the image."
    )
    body={
        "contents":[{"parts":[{"text":description}]}],
        "generationConfig":{"responseModalities":["IMAGE","TEXT"]}
    }
    url=("https://generativelanguage.googleapis.com/v1beta/models/"
         "gemini-2.5-flash-image:generateContent?key="+api_key)
    req=urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type":"application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req,timeout=120) as response:
            result=json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode("utf-8","ignore")[:500]
        app.logger.error("Error del servicio de imágenes: %s %s",exc.code,detail)
        raise ValueError("El servicio de imágenes no pudo crear la figura. Revisa GEMINI_API_KEY, el acceso al modelo y la cuota disponible.")
    except Exception:
        app.logger.exception("No se pudo conectar con el servicio de imágenes")
        raise ValueError("No se pudo conectar con el servicio de imágenes. Inténtalo de nuevo.")
    for candidate in result.get("candidates",[]):
        for part in candidate.get("content",{}).get("parts",[]):
            inline=part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                try:
                    raw=base64.b64decode(inline["data"])
                    if len(raw)>10*1024*1024:
                        raise ValueError("La figura generada supera el tamaño permitido.")
                    return decode_image(raw,"figura generada")
                except ValueError:
                    raise
                except Exception:
                    continue
    app.logger.error("Respuesta sin imagen: %s",str(result)[:1000])
    raise ValueError("El servicio no devolvió una imagen. Prueba con otra descripción.")

def image_for_poster(source,max_w,max_h):
    if not source:
        return None
    im=source.copy().convert("RGBA")
    im.thumbnail((max_w,max_h),Image.Resampling.LANCZOS)
    return im

def compose_design(data,logo=None,photo=None,figure=None,mode="qr"):
    bg=valid_color(data.get("background"),"#FFFFFF")
    fg=valid_color(data.get("foreground"),"#172033")
    accent=valid_color(data.get("accent"),"#2563EB")
    name=clean_text(data.get("name"))
    address=clean_text(data.get("address"))
    phone=clean_text(data.get("phone"))
    phrase=clean_text(data.get("phrase"))
    style=clean_text(data.get("style"),20)
    placement=clean_text(data.get("placement"),10)
    if style not in {"modern","elegant","friendly","classic"}:
        style="modern"
    if placement not in {"top","bottom"}:
        placement="top"

    payload=""
    qr_image=None
    if mode=="qr":
        payload=clean_text(data.get("text"),MAX_TEXT)
        if not payload:
            raise ValueError("Escribe una URL o un texto para el QR.")
        qr=qrcode.QRCode(
            version=None,error_correction=ERROR_CORRECT_H,
            box_size=20,border=4
        )
        qr.add_data(payload)
        qr.make(fit=True)
        qr_image=qr.make_image(fill_color=fg,back_color=bg).convert("RGB")
        qr_image=qr_image.resize((QR_SIZE,QR_SIZE),Image.Resampling.NEAREST)
        artwork=None
    else:
        if figure is None:
            raise ValueError("Primero genera una figura.")
        artwork=image_for_poster(figure,1500,1250)

    promo=photo if mode=="qr" else artwork
    promo=image_for_poster(promo,1500,1250) if promo else None
    logo=image_for_poster(logo,360,300) if logo else None

    width=POSTER_WIDTH
    pad=100
    title_h=100 if name else 30
    logo_h=(logo.height+55) if logo else 0
    promo_h=(promo.height+90) if promo else 0
    qr_h=QR_SIZE+100 if qr_image else 0
    info=[v for v in (address,phone,phrase) if v]
    info_h=sum(78 for _ in info)
    if mode=="figure":
        qr_h=0
        promo_h=max(promo_h,500)
    height=pad+title_h+logo_h+promo_h+qr_h+info_h+pad
    height=max(height,1500)
    image=Image.new("RGB",(width,height),bg)
    draw=ImageDraw.Draw(image)

    if style=="elegant":
        draw.rectangle((0,0,width,24),fill=accent)
        draw.rectangle((pad,55,width-pad,60),fill=accent)
    elif style=="friendly":
        draw.rounded_rectangle((28,28,width-28,height-28),radius=60,outline=accent,width=10)
    elif style=="classic":
        draw.rectangle((42,42,width-42,height-42),outline=accent,width=5)
    else:
        draw.rectangle((0,0,width,24),fill=accent)

    y=pad
    if name:
        font=fit_font(draw,name,width-2*pad,72,True,26)
        y=center_text(draw,name,y,font,fg,width)
    else:
        y+=20

    if logo:
        image.paste(logo,((width-logo.width)//2,y),logo)
        y+=logo.height+55

    if promo and placement=="top":
        image.paste(promo,((width-promo.width)//2,y),promo)
        y+=promo.height+70

    if qr_image:
        image.paste(qr_image,((width-QR_SIZE)//2,y))
        y+=QR_SIZE+65

    if promo and placement=="bottom":
        image.paste(promo,((width-promo.width)//2,y),promo)
        y+=promo.height+70

    for text,color,bold in (
        (address,fg,False),(phone,fg,False),(phrase,accent,True)
    ):
        if text:
            font=fit_font(draw,text,width-2*pad,43,bold,20)
            y=center_text(draw,text,y,font,color,width)+8

    if y+pad>height:
        expanded=Image.new("RGB",(width,y+pad),bg)
        expanded.paste(image,(0,0))
        image=expanded
        draw=ImageDraw.Draw(image)
        if style=="friendly":
            draw.rounded_rectangle((28,28,width-28,image.height-28),radius=60,outline=accent,width=10)
        elif style=="classic":
            draw.rectangle((42,42,width-42,image.height-42),outline=accent,width=5)
    return image,payload

def image_to_png(image):
    output=io.BytesIO()
    image.save(output,format="PNG",optimize=True,dpi=(300,300))
    return output.getvalue()

def request_design():
    mode=clean_text(request.form.get("mode","qr"),10)
    if mode not in {"qr","figure"}:
        mode="qr"
    logo=read_image(request.files.get("logo"),MAX_LOGO_BYTES,"El logotipo")
    photo=read_image(request.files.get("photo"),MAX_PHOTO_BYTES,"La fotografía")
    figure=read_figure_data(request.form.get("figure_image")) if mode=="figure" else None
    image,payload=compose_design(request.form,logo,photo,figure,mode)
    return image,payload

@app.get("/")
def index():
    return send_file("index.html")

@app.post("/generate_qr")
def generate_qr():
    try:
        image,payload=request_design()
        return jsonify({
            "image":"data:image/png;base64,"+base64.b64encode(image_to_png(image)).decode("ascii"),
            "payload":payload,
            "width":image.width,
            "height":image.height,
            "mode":request.form.get("mode","qr")
        })
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error creando diseño")
        return jsonify({"error":"No se pudo crear el diseño. Revisa los datos e inténtalo otra vez."}),500

@app.post("/generate_figure")
def generate_figure():
    try:
        prompt=clean_text(request.form.get("prompt"),700)
        if not prompt:
            raise ValueError("Describe la figura que deseas crear.")
        style=clean_text(request.form.get("art_style","ilustración profesional"),100)
        bg=valid_color(request.form.get("background"),"#FFFFFF")
        accent=valid_color(request.form.get("accent"),"#2563EB")
        figure=generate_ai_figure(prompt,style,bg,accent)
        raw=io.BytesIO()
        figure.save(raw,format="PNG",optimize=True)
        figure_data="data:image/png;base64,"+base64.b64encode(raw.getvalue()).decode("ascii")
        data=request.form.to_dict()
        data["mode"]="figure"
        logo=read_image(request.files.get("logo"),MAX_LOGO_BYTES,"El logotipo")
        poster,_=compose_design(data,logo,None,figure,"figure")
        return jsonify({
            "image":"data:image/png;base64,"+base64.b64encode(image_to_png(poster)).decode("ascii"),
            "figure_image":figure_data,
            "width":poster.width,
            "height":poster.height,
            "mode":"figure"
        })
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error generando figura")
        return jsonify({"error":"No se pudo generar la figura. Inténtalo de nuevo."}),500

def make_download():
    image,_=request_design()
    return image

@app.post("/download_png")
def download_png():
    try:
        image=make_download()
        output=io.BytesIO(image_to_png(image))
        output.seek(0)
        return send_file(output,mimetype="image/png",as_attachment=True,download_name="diseno-personalizado.png")
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PNG")
        return jsonify({"error":"No se pudo preparar el PNG."}),500

@app.post("/download_pdf")
def download_pdf():
    try:
        image=make_download()
        png=io.BytesIO(image_to_png(image))
        png.seek(0)
        output=io.BytesIO()
        # PDF de alta calidad con proporción del cartel, sin deformar la imagen.
        page_w=612.0
        page_h=page_w*image.height/image.width
        pdf=canvas.Canvas(output,pagesize=(page_w,page_h),pageCompression=1)
        pdf.drawImage(ImageReader(png),0,0,width=page_w,height=page_h,mask="auto")
        pdf.showPage()
        pdf.save()
        output.seek(0)
        return send_file(output,mimetype="application/pdf",as_attachment=True,download_name="diseno-personalizado.pdf")
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    except Exception:
        app.logger.exception("Error descargando PDF")
        return jsonify({"error":"No se pudo preparar el PDF."}),500

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error":"Los archivos son demasiado grandes. Reduce la fotografía o el logotipo e inténtalo de nuevo."}),413

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","5000")),debug=False)
