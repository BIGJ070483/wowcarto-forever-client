"""Prepare local installer artwork from the already packaged map preview."""
from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def build(preview: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    large, small = output / 'wizard-large.bmp', output / 'wizard-small.bmp'
    if large.exists() or small.exists():
        raise FileExistsError('Les illustrations de destination existent déjà.')
    size = (328, 628)
    image = Image.new('RGB', size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        t = y / (size[1] - 1)
        draw.line((0,y,size[0],y), fill=(int(11+8*t),int(28+12*t),int(36+9*t)))
    draw.rectangle((12,12,315,615), outline='#b69a62', width=2)
    draw.line((30,37,297,37), fill='#7c724e', width=2)
    draw.line((30,581,297,581), fill='#7c724e', width=2)
    font_root = Path('C:/Windows/Fonts')
    bold = ImageFont.truetype(str(font_root / 'segoeuib.ttf'), 38)
    wide = ImageFont.truetype(str(font_root / 'segoeui.ttf'), 25)
    body = ImageFont.truetype(str(font_root / 'segoeui.ttf'), 19)
    def centered(y, text, font, color):
        box=draw.textbbox((0,0),text,font=font)
        draw.text(((size[0]-(box[2]-box[0]))/2,y),text,font=font,fill=color)
    centered(64,'WoWCarto',bold,'#f0e1ba')
    centered(113,'FOREVER',wide,'#d8b96e')
    source=Image.open(preview).convert('RGBA')
    source.thumbnail((240,240),Image.Resampling.LANCZOS)
    source=source.resize((220,220),Image.Resampling.LANCZOS)
    image.paste(source,((size[0]-220)//2,210),source)
    draw=ImageDraw.Draw(image)
    centered(490,'Communauté',body,'#d9e2df')
    centered(521,'Community',body,'#d9e2df')
    image.resize((164,314),Image.Resampling.LANCZOS).save(large,format='BMP')
    icon=Image.open(preview).convert('RGB').resize((55,55),Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(55,58),'#10252c'); canvas.paste(icon,(0,1)); canvas.save(small,format='BMP')
    return large, small


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--preview',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    for path in build(args.preview,args.output): print(path)
