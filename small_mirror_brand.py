"""Public product identity. Personal enrollment stays in an isolated local directory."""
PRODUCT_CN='窥贼'
PRODUCT_EN='Crazy'
PRODUCT_TITLE=f'{PRODUCT_CN} · {PRODUCT_EN}'
VERSION_LABEL='v13.4 Beta'
USE_NOTICE='Beta 测试版 · 仅供娱乐 · 未商业化'
SLOGAN='把背后交给我'


def logo_image(size=128):
    """Original product mark, shared by the window, tray and executable."""
    from PIL import Image, ImageDraw
    image=Image.new('RGBA',(512,512),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,511,511),112,fill='#C1D8AB')
    draw.ellipse((88,152,424,360),fill='#1B2420')
    draw.ellipse((192,192,320,320),fill='#C1D8AB')
    return image.resize((size,size),Image.Resampling.LANCZOS)


def logo_icon_resource(size=32):
    """Return one native DIB icon resource, generated entirely in memory."""
    from io import BytesIO
    import struct
    output=BytesIO()
    logo_image(size).save(output,format='ICO',sizes=[(size,size)],bitmap_format='bmp')
    data=output.getvalue()
    length,offset=struct.unpack_from('<II',data,14)
    return data[offset:offset+length]
