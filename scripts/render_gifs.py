"""Create full-game looping GIF previews from published replay videos."""
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    for video in sorted((ROOT/'docs/games').glob('*/replay.mp4')):
        output=video.with_suffix('.gif')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(video),
                        '-filter_complex_threads','1','-filter_complex',
                        'fps=1,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
                        '-threads','2','-loop','0','-final_delay','300',str(output)],check=True)
        print(f'{output.relative_to(ROOT)}: {output.stat().st_size/1024/1024:.2f} MiB',flush=True)


if __name__=='__main__':
    main()
