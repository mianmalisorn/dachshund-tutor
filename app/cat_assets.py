"""Private Mali Cat registry. Only manifest IDs reach the authenticated widget route."""
import hashlib
import json
import logging
import random
import re
from .config import ROOT

ASSET_ROOT = ROOT / 'app/private_assets/mali_cat'
MANIFEST = ASSET_ROOT / 'manifest.json'

def load_manifest(path=None, verify_files=False):
    path = path or MANIFEST
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or data.get('audience') != 'admin_only':
        raise ValueError('Invalid Mali Cat manifest')
    ids, paths = set(), set()
    for item in data['assets']:
        key, relative, category = item['id'], item['path'], item['category']
        if (not re.fullmatch(r'[a-z0-9_]+', key) or key in ids or relative in paths
                or category not in ('emotions','poses','branding')
                or not re.fullmatch(category+r'/[a-z0-9_]+\.png', relative)
                or not isinstance(item['contexts'],list)
                or any(not isinstance(context,str) or not re.fullmatch(r'[a-z_]+',context) for context in item['contexts'])
                or not set(item['modes']) <= {'normal','rare_care','easter_egg'}
                or (category == 'branding' and (item['modes'] or item['contexts']))
                or (category == 'poses' and 'normal' in item['modes'])):
            raise ValueError('Invalid Mali Cat entry')
        ids.add(key); paths.add(relative)
        if verify_files:
            from PIL import Image
            target=(path.parent / relative).resolve()
            if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
                raise ValueError('Missing or outside Mali Cat asset')
            content=target.read_bytes()
            if hashlib.sha256(content).hexdigest()!=item['sha256'] or len(content)!=item['bytes']:
                raise ValueError('Mali Cat checksum mismatch')
            with Image.open(target) as image:
                image.load()
                if image.format!='PNG' or list(image.size)!=[item['width'],item['height']] or image.mode!=item['mode']:
                    raise ValueError('Mali Cat metadata mismatch')
    if verify_files:
        actual={p.relative_to(path.parent).as_posix() for p in path.parent.rglob('*.png')}
        if actual != paths:raise ValueError('Mali Cat manifest does not cover every PNG')
    return data

try:
    ITEMS = {item['id']:item for item in load_manifest()['assets']}
except (OSError, ValueError, KeyError, TypeError):
    logging.getLogger(__name__).warning('Mascot assets unavailable: invalid_or_missing_manifest')
    ITEMS = {}
FILES = {key:item['path'] for key,item in ITEMS.items()}
EMOTIONS = tuple(key for key,item in ITEMS.items() if item['category']=='emotions')
POSES = tuple(key for key,item in ITEMS.items() if item['category']=='poses')
BRANDING = tuple(key for key,item in ITEMS.items() if item['category']=='branding')
APPROVED_WIDGET_ASSETS = frozenset(EMOTIONS + POSES)
WIDGET_ASSET_MAP = {context:tuple(key for key,item in ITEMS.items() if context in item['contexts'])
                    for item in ITEMS.values() for context in item['contexts']}

def asset_path(key):
    relative = FILES.get(key)
    if relative is None:return None
    path = (ASSET_ROOT / relative).resolve()
    if not path.is_relative_to(ASSET_ROOT.resolve()) or not path.is_file():return None
    return path

def candidates(context_key, mode='normal'):
    keys=WIDGET_ASSET_MAP.get(context_key, ())
    if isinstance(keys,str):keys=(keys,)
    return [key for key in keys if key in APPROVED_WIDGET_ASSETS and asset_path(key)
            and mode in ITEMS.get(key,{'modes':['normal','rare_care','easter_egg']})['modes']]

def widget_asset_key(context_key, mode='normal'):
    """Deterministic availability probe; never consumes random selection."""
    pool=candidates(context_key,mode)
    return pool[0] if pool else None

def choose_widget_asset(context_key, mode='normal', previous=None):
    pool=candidates(context_key,mode)
    alternatives=[key for key in pool if key!=previous]
    if alternatives:pool=alternatives
    # Pose eligibility follows an already selected rare dialogue, never a normal event.
    poses=[key for key in pool if key in POSES]
    if poses and (not any(key in EMOTIONS for key in pool) or random.random()<0.5):pool=poses
    return random.choice(pool) if pool else None

def private_release_files():
    """Fail the build before writing a ZIP when a required private file is absent."""
    manifest=load_manifest(verify_files=True)
    from .mascot import load_catalog
    load_catalog()
    files=[MANIFEST,ROOT/'app/private_assets/dialogues.json',ROOT/'app/private_assets/placeholder.svg']
    files += [ASSET_ROOT/item['path'] for item in manifest['assets']]
    if any(not path.is_file() for path in files):raise ValueError('Missing private release asset')
    return files
