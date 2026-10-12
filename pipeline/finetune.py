"""Fine-tune Cellpose on your own labels and compare it with the base pipeline on held-out images.

  python pipeline/finetune.py --prep-only            # only build masks from the labels + check images (no Cellpose needed)
  python pipeline/finetune.py --epochs 2             # smoke test: does it run, how long does one epoch take
  python pipeline/finetune.py --epochs 100           # real training + comparison
  python pipeline/finetune.py --eval-only --model finetune_out/models/spheroid_v1

Training uses images that have at least one accepted or hand-drawn spheroid (images where everything was rejected
and images marked "empty" are skipped). --holdout of them are kept out of training and used only for the comparison
(same --seed = same images). Images are downscaled by --scale for training and inference (default 0.5).

The labels file is the labels.json exported by pipeline/make_review_page.py.
"""
import argparse, json, os, random, sys, time
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spheroid_pipeline as sp


def raster(polys, size):
    """Instance mask (ids 1..n) from polygons. size = (W, H)."""
    m = Image.new('I', size, 0)
    d = ImageDraw.Draw(m)
    for k, poly in enumerate(polys, 1):
        d.polygon([tuple(p) for p in poly], fill=k)
    return np.asarray(m).astype(np.int32)


def load_items(labels, root, exclude=()):
    items = []
    for o in json.load(open(labels))['images']:
        stem = os.path.splitext(os.path.basename(o['path']))[0]
        if stem in exclude:
            print('excluded from training:', stem)
            continue
        polys = [c['polygon'] for c in o['accepted']] + list(o['added'])
        if not polys:
            continue
        p = os.path.join(root, o['path'])
        img = np.asarray(Image.open(p).convert('RGB'))
        H, W = img.shape[:2]
        items.append(dict(path=p, name=os.path.splitext(os.path.basename(p))[0], img=img,
                          gt=raster(polys, (W, H)), n=len(polys)))
    return items


def outline(img, labels, color, width=3):
    out = img.copy()
    for k in range(1, int(labels.max()) + 1):
        m = labels == k
        e = m & ~ndi.binary_erosion(m, iterations=width)
        out[e] = color
    return out


def save_half(arr, path):
    im = Image.fromarray(arr)
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(path, quality=88)


def small(img, s):
    return np.asarray(Image.fromarray(img).resize((round(img.shape[1] * s), round(img.shape[0] * s)), Image.LANCZOS))


def small_mask(m, s):
    return np.asarray(Image.fromarray(m).resize((round(m.shape[1] * s), round(m.shape[0] * s)), Image.NEAREST)).astype(np.int32)


def match(preds, gt, thr=0.5):
    """Greedy IoU matching. preds: list of boolean masks, gt: label image."""
    G = [gt == k for k in range(1, int(gt.max()) + 1)]
    pairs = []
    for i, p in enumerate(preds):
        for j, g in enumerate(G):
            inter = np.logical_and(p, g).sum()
            if inter:
                pairs.append((inter / np.logical_or(p, g).sum(), i, j))
    pairs.sort(reverse=True)
    usedp, usedg, ious = set(), set(), []
    for iou, i, j in pairs:
        if iou < thr or i in usedp or j in usedg:
            continue
        usedp.add(i); usedg.add(j); ious.append(iou)
    tp = len(ious)
    return tp, len(preds) - tp, len(G) - tp, ious


def train(args, tr, te):
    from cellpose import models, train as ctrain, io as cio
    cio.logger_setup()      # show per-epoch progress
    model = models.CellposeModel(gpu=True)
    t0 = time.time()
    path, trl, tel = ctrain.train_seg(
        model.net,
        train_data=[small(i['img'], args.scale) for i in tr],
        train_labels=[small_mask(i['gt'], args.scale) for i in tr],
        test_data=[small(i['img'], args.scale) for i in te] or None,
        test_labels=[small_mask(i['gt'], args.scale) for i in te] or None,
        batch_size=1, learning_rate=args.lr, weight_decay=0.1, n_epochs=args.epochs,
        nimg_per_epoch=args.per_epoch, min_train_masks=1,
        save_path=args.out, model_name=args.name)
    print(f'training finished: {time.time() - t0:.0f} s -> {path}')
    return str(path)


def evaluate(args, te, model_path):
    from cellpose import models
    ft = models.CellposeModel(gpu=True, pretrained_model=model_path)
    os.makedirs(os.path.join(args.out, 'holdout'), exist_ok=True)
    tot = {'base': [0, 0, 0, []], 'ft': [0, 0, 0, []]}
    print(f"\n{'image':34s} {'GT':>3s} | {'base TP/FP/FN':>13s} | {'fine-tuned TP/FP/FN':>19s}")
    for it in te:
        cache = it['path'].rsplit('.', 1)[0] + '_cp40.npy'
        M_base = np.load(cache) if os.path.exists(cache) else sp.run_cellpose(models.CellposeModel(gpu=True), it['img'], 40)
        M_ft = sp.run_scaled(ft, it['img'], args.scale, args.cellprob)
        line = []
        for tag, M in (('base', M_base), ('ft', M_ft)):
            cands = [m for m, _ in sp.merge(M)]
            tp, fp, fn, ious = match(cands, it['gt'])
            t = tot[tag]; t[0] += tp; t[1] += fp; t[2] += fn; t[3] += ious
            line.append(f'{tp}/{fp}/{fn}')
            pred = np.zeros(M.shape, np.int32)
            for k, m in enumerate(cands, 1):
                pred[m] = k
            vis = outline(outline(it['img'], it['gt'], (0, 220, 0)), pred, (255, 220, 0))
            save_half(vis, os.path.join(args.out, 'holdout', f"{it['name'].replace(' ', '_')}_{tag}.jpg"))
        print(f"{it['name'][:34]:34s} {it['n']:3d} | {line[0]:>13s} | {line[1]:>19s}")
    print('\n                precision  recall  F1     mean IoU   (IoU>=0.5)')
    for tag, label in (('base', 'base pipeline'), ('ft', 'fine-tuned  ')):
        tp, fp, fn, ious = tot[tag]
        pr = tp / max(tp + fp, 1); rc = tp / max(tp + fn, 1); f1 = 2 * pr * rc / max(pr + rc, 1e-9)
        print(f'{label}   {pr:8.2f}  {rc:6.2f}  {f1:5.2f}  {np.mean(ious) if ious else 0:8.2f}')
    print(f"\nImages: {os.path.join(args.out, 'holdout')}/  (green = your label, yellow = model)")


def main():
    ap = argparse.ArgumentParser(description='Fine-tune Cellpose on spheroid labels and compare with the base pipeline.')
    ap.add_argument('--labels', default='labels.json')
    ap.add_argument('--root', default='.', help='folder that the image paths in the labels file are relative to')
    ap.add_argument('--out', default='finetune_out')
    ap.add_argument('--name', default='spheroid_v1', help='name of the saved model')
    ap.add_argument('--epochs', type=int, default=100)
    ap.add_argument('--lr', type=float, default=1e-5)
    ap.add_argument('--per-epoch', type=int, default=None, help='samples per epoch (default: number of training images)')
    ap.add_argument('--scale', type=float, default=0.5)
    ap.add_argument('--holdout', type=int, default=5, help='number of labeled images kept out for the comparison')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--cellprob', type=float, default=0.0)
    ap.add_argument('--exclude', nargs='*', default=[], help='image names (no extension, exact) to leave out of training and comparison')
    ap.add_argument('--prep-only', action='store_true')
    ap.add_argument('--eval-only', action='store_true')
    ap.add_argument('--model', default=None)
    args = ap.parse_args()

    items = load_items(args.labels, args.root, set(args.exclude))
    idx = list(range(len(items)))
    random.Random(args.seed).shuffle(idx)
    hold = set(idx[:args.holdout])
    tr = [it for k, it in enumerate(items) if k not in hold]
    te = [it for k, it in enumerate(items) if k in hold]
    print(f'{len(items)} labeled images, {sum(i["n"] for i in items)} spheroids -> train {len(tr)}, comparison {len(te)}')
    print('comparison images:', ', '.join(i['name'] for i in te))

    os.makedirs(os.path.join(args.out, 'gt_check'), exist_ok=True)
    for it in items:
        save_half(outline(it['img'], it['gt'], (0, 220, 0)), os.path.join(args.out, 'gt_check', it['name'].replace(' ', '_') + '.jpg'))
    print(f"label mask check images: {os.path.join(args.out, 'gt_check')}/")
    if args.prep_only:
        return
    if args.eval_only:
        model_path = args.model or os.path.join(args.out, 'models', args.name)
    else:
        model_path = train(args, tr, te)
    evaluate(args, te, model_path)


if __name__ == '__main__':
    main()
