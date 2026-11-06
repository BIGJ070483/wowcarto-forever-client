"""Build verified small previews beside the private copied Forever assets."""
import json
from pathlib import Path
import sys
from PIL import Image
from tools.map_packs import sha256

root = Path(sys.argv[1])
data = json.loads((root / 'pack.json').read_text(encoding='utf-8'))
output = root / 'previews'
output.mkdir(exist_ok=False)
result = {}
for index, row in enumerate(data['maps'], 1):
    source = root / row['standard']['path']
    if sha256(source) != row['standard']['sha256']:
        raise ValueError('Texture divergente : ' + str(source))
    with Image.open(source) as image:
        image.load()
        image = image.convert('RGB')
        image.thumbnail((64, 64), Image.Resampling.LANCZOS)
        target = output / (source.stem + '.png')
        image.save(target)
        result[row['id']] = dict(path='previews/' + target.name, width=image.width, height=image.height,
                                 bytes=target.stat().st_size, sha256=sha256(target))
    if index % 200 == 0:
        print(index, '/', len(data['maps']), flush=True)
with (root / 'previews.json').open('x', encoding='utf-8') as stream:
    json.dump(result, stream, ensure_ascii=False)
print('PASS', len(result), flush=True)
