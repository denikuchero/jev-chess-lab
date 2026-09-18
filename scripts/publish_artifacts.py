"""Export public research records and render annotated PGN replays (not live footage)."""
import copy
import argparse
import html
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import cairosvg
import chess
import chess.svg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
GAMES = [
    ('chess-first-game', '01-raw', 'Jev самостоятельно: первая партия', 'Все легальные ходы. Без тактических подсказок.'),
    ('chess-guarded-v2', '02-guarded', 'Jev + локальный фильтр', 'Код исключает обнаруженные потери материала.'),
    ('chess-coach-v3-skill5', '03-stockfish-assisted', 'Jev + Stockfish: опыт с помощником', 'Stockfish отбирает хорошие ходы. Это не чистая Jev.'),
    ('chess-pure-v4', '04-pure-history', 'Jev самостоятельно: история и инструкция', 'Все легальные ходы. Без помощников и отсева зевков.'),
    ('chess-pure-v5', '05-pure-explicit-pieces', 'Jev самостоятельно: явное описание фигур', 'Клетки и фигуры словами. Без оценки и фильтра ходов.'),
    ('chess-deliberate-v6', '06-jev-review-loop', 'Jev: предложение, проверка, выбор', 'Все оценки даёт Jev. Без шахматного советчика.'),
]


def public_copy(value):
    if isinstance(value, dict):
        return {k: public_copy(v) for k, v in value.items()
                if not (k == 'id' and 'answers' in value)}
    if isinstance(value, list):
        return [public_copy(v) for v in value]
    return value


def draw_frame(board, row, title, subtitle, summary, output):
    image = Image.new('RGB', (1280, 800), '#111b2b')
    draw = ImageDraw.Draw(image)
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 17)
    heading = ImageFont.truetype(font_path, 27)
    draw.text((25, 18), title, font=heading, fill='white')
    draw.text((25, 59), subtitle, font=small, fill='#b8c6df')
    svg = chess.svg.board(board, lastmove=chess.Move.from_uci(row['uci']) if row else None, size=680)
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=680, output_height=680)
    image.paste(Image.open(io.BytesIO(png)).convert('RGB'), (20, 95))
    def text(y, value, color='white', f=font):
        draw.text((735,y),value,font=f,fill=color)
    text(120, 'БЕЛЫЕ: JEV')
    text(160, 'ЧЁРНЫЕ: ' + ('Stockfish Skill 5' if 'Stockfish' in subtitle else 'локальный бот'), f=small)
    if row:
        number=(row['ply']+1)//2
        text(220, f'{number}{"." if row["ply"]%2 else "..."} {row["san"]}', f=heading)
        text(268, f'{row["actor"]}: {row["uci"]}')
        text(306, f'Решение: {row["elapsed_s"]:.2f} с', f=small)
    else:
        text(220,'Начальная позиция')
    text(380, 'Итог партии: ' + summary['result'])
    text(420, f'Запросов Jev: {summary["api_requests"]}', f=small)
    text(454, f'Входных токенов: {summary["input_tokens"]:,}', f=small)
    text(488, f'API: ${summary["reported_cost_usd"]:.6f}', f=small)
    text(575, 'Запись восстановлена из ходов.', '#f5cf81', small)
    text(605, '1 полуход/с; это НЕ live-видео.', '#f5cf81', small)
    text(655, 'Одна партия не определяет рейтинг.', '#b8c6df', small)
    text(690, 'github.com/denikuchero/jev-chess-lab', '#b8c6df', small)
    image.save(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-media',action='store_true',help='Keep existing videos/posters; only render missing ones')
    args=parser.parse_args()
    cards=[]
    manifest=[]
    for source, slug, title, subtitle in GAMES:
        src=ROOT/'results'/source
        dest=ROOT/'docs/games'/slug
        if not (src/'game.json').exists():
            src=dest
        dest.mkdir(parents=True,exist_ok=True)
        raw=json.loads((src/'game.json').read_text())
        data=public_copy(copy.deepcopy(raw))
        (dest/'game.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        for name in ('game.pgn','replay.html'):
            if src.resolve() != dest.resolve():
                shutil.copyfile(src/name,dest/name)
        page=(dest/'replay.html').read_text()
        page=page.replace('<title>Jev играет в шахматы</title>',f'<title>{html.escape(title)}</title>')
        if slug=='04-pure-history':
            page=page.replace('<h1>Jev — белые; простой бот — чёрные</h1>',f'<h1>{title}</h1>')
        (dest/'replay.html').write_text(page,encoding='utf-8')
        if not (args.reuse_media and (dest/'replay.mp4').exists() and (dest/'poster.png').exists()):
            with tempfile.TemporaryDirectory(prefix='jev-video-') as tmp:
                folder=Path(tmp)
                board=chess.Board()
                rows=[None]+data['moves']
                for i,row in enumerate(rows):
                    if row:
                        move=chess.Move.from_uci(row['uci'])
                        assert move in board.legal_moves
                        board.push(move)
                    draw_frame(board,row,title,subtitle,data['summary'],folder/f'{i:04}.png')
                shutil.copyfile(folder/'0000.png',dest/'poster.png')
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-framerate','1',
                                '-i',str(folder/'%04d.png'),'-vf','fps=24','-c:v','libx264',
                                '-preset','veryfast','-threads','2','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',
                                str(dest/'replay.mp4')],check=True)
        stats=data['summary']
        manifest.append(dict(slug=slug,title=title,summary=stats))
        cards.append(f'''<article><h2>{html.escape(title)}</h2><p>{html.escape(subtitle)}</p>
<video controls preload="none" poster="games/{slug}/poster.png" src="games/{slug}/replay.mp4"></video>
<p>Результат: <b>{stats['result']}</b> · запросов: {stats['api_requests']} · стоимость API: ${stats['reported_cost_usd']:.6f}</p>
<p><a href="games/{slug}/replay.html">Интерактивная доска</a> · <a href="games/{slug}/replay.gif">GIF</a> · <a href="games/{slug}/replay.mp4">Видео MP4</a> · <a href="games/{slug}/game.pgn">PGN</a> · <a href="games/{slug}/game.json">Запросы и ответы</a></p></article>''')
        print(f'Exported {slug}: {stats["result"]}',flush=True)
    (ROOT/'docs/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'docs/.nojekyll').touch()
    (ROOT/'docs/index.html').write_text('''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jev Chess Lab — шахматные эксперименты</title><style>body{max-width:1100px;margin:40px auto;padding:0 20px;background:#111b2b;color:#eaf0ff;font:18px system-ui;line-height:1.55}a{color:#9edbff}article{padding:24px;background:#1c2b41;border-radius:16px;margin:26px 0}video{width:100%;border-radius:10px}h1{line-height:1.2}</style>
<h1>Jev Chess Lab</h1><p>Самостоятельная Jev, фильтр разменов, помощь Stockfish и эксперименты с представлением позиции.</p>
<p><b>Победа со Stockfish — результат системы с движком, а не доказательство силы Jev.</b> Мы публикуем и успехи, и неудачи; итоги всех самостоятельных партий показаны ниже.</p>
<p>Видео восстановлены из сохранённых легальных ходов: один полуход в секунду. Это визуализация партий, не запись экрана и не скорость реальной игры. Время решений указано отдельно. Стоимость включает только API, не вычисления локального движка.</p>
<p><a href="https://github.com/denikuchero/jev-chess-lab">Код, методика и ограничения на GitHub</a></p>'''+''.join(cards)+'''<p>Одна партия на вариант; соперники и условия различаются. Это учебное исследование, не рейтинг Elo, не обучение весов Jev и не контролируемый бенчмарк.</p></html>''',encoding='utf-8')


if __name__=='__main__':
    main()
