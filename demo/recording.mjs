/**
 * What the two recorders share: Playwright, Python, an ffmpeg that writes MPEG, the
 * cues a soundtrack is built from, and the encoding itself.
 *
 * `record.mjs` films the shop's page and `benchmark.mjs` the benchmark's. Each one
 * decides what happens on screen; everything after the last frame is here.
 */
import { spawnSync, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);

/** The repository, which both Python modules are run from. */
export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/** Playwright, from wherever it is installed -- locally, or globally as here. */
export function playwright() {
  try {
    return require('playwright');
  } catch {
    const global = execFileSync('npm', ['root', '-g'], {
      encoding: 'utf8',
    }).trim();
    return require(join(global, 'playwright'));
  }
}

/** `--name value` pairs, and a bare `--name` as present with an empty value. */
export function parseArgs(argv = process.argv.slice(2)) {
  return Object.fromEntries(
    argv.reduce((pairs, value, index, all) => {
      if (value.startsWith('--')) {
        const next = all[index + 1];
        pairs.push([value.slice(2), next === undefined || next.startsWith('--') ? '' : next]);
      }
      return pairs;
    }, []),
  );
}

/**
 * A Python module run from the repository, under whichever of `python` and
 * `python3` answers: the result of the first that exits 0.
 */
export function python(args, { input } = {}) {
  let failure;
  for (const interpreter of ['python', 'python3']) {
    const ran = spawnSync(interpreter, args, {
      cwd: root,
      input,
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    if (ran.status === 0) return ran.stdout;
    failure = ran.stderr || ran.error?.message;
  }
  throw new Error(`python ${args.join(' ')} failed:\n${failure ?? ''}`);
}

/**
 * An ffmpeg that can write an MPEG program stream.
 *
 * Playwright ships one beside its browsers, but that build is stripped down to
 * what recording needs and has neither the `mpeg` muxer nor the `mpeg1video`
 * encoder, so a system ffmpeg is preferred and the bundled one is only a last
 * resort. `--ffmpeg` names a third.
 */
export function ffmpegBinary(named) {
  if (named) return named;
  for (const candidate of ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']) {
    if (existsSync(candidate)) return candidate;
  }
  const browsers = process.env.PLAYWRIGHT_BROWSERS_PATH ?? '/opt/pw-browsers';
  for (const candidate of ['ffmpeg-1011/ffmpeg-linux', 'ffmpeg/ffmpeg-linux']) {
    const path = join(browsers, candidate);
    if (existsSync(path)) return path;
  }
  return 'ffmpeg';
}

/**
 * How the picture is written: MPEG-2 video in a program stream, still a `.mpg`.
 *
 * MPEG-1 is what the first two recordings used and it is the wrong format for
 * this picture. 1280x720 is far outside MPEG-1's constrained parameters, so the
 * encoder declares a video buffer smaller than a single one of its own
 * keyframes and every pack the muxer writes violates the system target decoder.
 * A lenient player ignores all of that and shows the film; a player that has to
 * schedule an audio track against the same model gives up and opens nothing.
 *
 * So the rate and the buffer are stated rather than left to `-q:v`, and the
 * codec is the one whose levels this frame size is inside. An MPEG-2 program
 * stream is the DVD lineage -- the format with the widest player support there
 * is -- and it is what `.mpg` means to everything that reads one.
 *
 * The program stream has to be MPEG-2 too. ffmpeg's `mpeg` muxer writes the
 * MPEG-1 system layer whatever video it is handed, and a player that goes by the
 * pack headers -- Windows' own does -- takes the file for MPEG-1, finds MPEG-2
 * video in it, and plays nothing; a lenient one never noticed. `vob` writes MPEG-2
 * packs, the same stream DVDs carry, without asking for anything else of DVD.
 */
export const VIDEO = video();

/**
 * The same, at another average rate and keyframe spacing.
 *
 * The shop's recordings run for seconds and change on most of them. The
 * benchmark's runs for minutes over a page that mostly sits still, where 3 Mbit/s
 * and a keyframe every twelfth frame buy nothing but size: about 100 MB, which is
 * more than GitHub takes in one file. A keyframe of a page of text is the dear
 * part, so it is spaced out, as far as a player seeking in it will bear.
 */
export function video({ rate = '3000k', keyframes = 12 } = {}) {
  return [
    '-c:v',
    'mpeg2video',
    '-b:v',
    rate,
    '-g',
    String(keyframes),
    // The buffer is Main Level's, which every MPEG-2 decoder has. At a 3.5 Mbit/s
    // ceiling a scroll through a page of text still drained it ("rc buffer
    // underflow"); 6 Mbit/s refills it in time and is far inside the level's limit.
    '-maxrate',
    '6000k',
    '-bufsize',
    '1835008',
    '-r',
    '25',
    '-f',
    'vob',
  ];
}

/**
 * MP2 is the audio an MPEG program stream carries, so a recording with sound in it
 * is still the one format that plays anywhere: at 48 kHz in stereo, as on a DVD,
 * since 44.1 kHz mono is legal in the stream and still not what every decoder
 * of one expects. `demo/sound.py` writes 44.1 kHz mono; ffmpeg converts it.
 */
export const AUDIO = ['-c:a', 'mp2', '-b:a', '192k', '-ar', '48000', '-ac', '2'];

/**
 * Every cue so far, in seconds from the first frame.
 *
 * `start()` is called when Playwright opens the page, which is when it starts
 * the recording; a cue is whatever happened and when, plus anything its kind
 * needs (a spoken line carries its clip).
 */
export class Cues {
  constructor() {
    this.list = [];
    this.firstFrame = Date.now();
  }

  start() {
    this.firstFrame = Date.now();
  }

  now() {
    return (Date.now() - this.firstFrame) / 1000;
  }

  add(kind, extra = {}) {
    this.list.push({ kind, at: this.now(), ...extra });
  }
}

/**
 * How long the takes run for, decoded rather than read off a header.
 *
 * A soundtrack has to be exactly as long as the picture it goes under, and a
 * WebM that Playwright is still writing when the context closes carries a
 * duration that is anywhere from wrong to absent. Decoding to nowhere costs a
 * second and answers with the timestamp of the last frame, which is the clock
 * the cues were taken against.
 */
export function videoSeconds(ffmpeg, paths) {
  return paths.reduce((total, path) => {
    const probe = spawnSync(ffmpeg, ['-i', path, '-f', 'null', '-'], {
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    const stamps = [...(probe.stderr?.toString() ?? '').matchAll(/time=(\d+):(\d+):([\d.]+)/g)];
    const last = stamps.at(-1);
    if (last === undefined) throw new Error(`could not measure ${path}`);
    return total + Number(last[1]) * 3600 + Number(last[2]) * 60 + Number(last[3]);
  }, 0);
}

/**
 * The cues, as a WAV of that same length -- synthesised by `demo/sound.py`.
 *
 * Python again, for the reason the recorders already read the scripts with it:
 * what the demo sounds like is the demo's to say, and there is one interpreter
 * here that already has to be on PATH.
 */
export function soundtrack(cues, seconds, directory) {
  const track = join(directory, 'track.wav');
  python(['-m', 'demo.sound', '--duration', String(seconds), '--out', track], {
    input: JSON.stringify(cues),
  });
  return track;
}

/**
 * What ffmpeg says when the stream it wrote breaks the buffer model a player
 * schedules by: the encoder's rate control, or the muxer's system target decoder.
 * A lenient player shows such a file anyway, so the warning is the only notice.
 */
const BROKEN_STREAM = /underflow|overflow|non[- ]monoton/i;

/** Run ffmpeg, throwing its own account of what went wrong, warnings included. */
export function encode(ffmpeg, args) {
  const ran = spawnSync(ffmpeg, ['-y', '-hide_banner', '-loglevel', 'warning', ...args], {
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  const said = ran.stderr?.toString() ?? '';
  if (ran.status !== 0) throw new Error(`ffmpeg failed:\n${said}`);
  const broken = said.split('\n').filter((line) => BROKEN_STREAM.test(line));
  if (broken.length) {
    throw new Error(`ffmpeg wrote a stream players may refuse:\n${broken.slice(0, 10).join('\n')}`);
  }
}
