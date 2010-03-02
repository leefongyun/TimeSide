#!/usr/bin/env python
# -*- coding: utf-8 -*-

# wav2png.py -- converts wave files to wave file and spectrogram images
# Copyright (C) 2008 MUSIC TECHNOLOGY GROUP (MTG)
#                    UNIVERSITAT POMPEU FABRA
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#   Bram de Jong <bram.dejong at domain.com where domain in gmail>
#   Guillaume Pellerin <yomguy@parisson.com>


import optparse, math, sys
import ImageFilter, ImageChops, Image, ImageDraw, ImageColor
import numpy
import scikits.audiolab as audiolab
from timeside.core import FixedSizeInputAdapter
import Queue


color_schemes = {
    'default': {
        'waveform': [(50,0,200), (0,220,80), (255,224,0), (255,0,0)],
        'spectrogram': [(0, 0, 0), (58/4,68/4,65/4), (80/2,100/2,153/2), (90,180,100),
                      (224,224,44), (255,60,30), (255,255,255)]
    },
    'iso': {
        'waveform': [(0,0,255), (0,255,255), (255,255,0), (255,0,0)],
        'spectrogram': [(0, 0, 0), (58/4,68/4,65/4), (80/2,100/2,153/2), (90,180,100),
                      (224,224,44), (255,60,30), (255,255,255)]
    },
    'purple': {
        'waveform': [(173,173,173), (147,149,196), (77,80,138), (108,66,0)],
        'spectrogram': [(0, 0, 0), (58/4,68/4,65/4), (80/2,100/2,153/2), (90,180,100),
                      (224,224,44), (255,60,30), (255,255,255)]
    }
}


class SpectralCentroid(object):

    def __init__(self, fft_size, nframes, samplerate, window_function=numpy.ones):
        self.fft_size = fft_size
        self.buffer_size = self.fft_size
        self.window = window_function(self.fft_size)
        self.spectrum_range = None
        self.lower = 100
        self.higher = 22050
        self.lower_log = math.log10(self.lower)
        self.higher_log = math.log10(self.higher)
        self.clip = lambda val, low, high: min(high, max(low, val))
        self.q = Queue.Queue()
        self.nframes = nframes
        self.samplerate = samplerate
        self.spectrum_adapter = FixedSizeInputAdapter(self.fft_size, 1, pad=True)

    def process(self, frames, eod, spec_range=120.0):

        for buffer, end in self.spectrum_adapter.process(frames, True):
            samples = buffer[:,0].copy()
            if end:
                break
        samples *= self.window
        fft = numpy.fft.fft(samples)
        spectrum = numpy.abs(fft[:fft.shape[0] / 2 + 1]) / float(self.fft_size) # normalized abs(FFT) between 0 and 1
        length = numpy.float64(spectrum.shape[0])

        # scale the db spectrum from [- spec_range db ... 0 db] > [0..1]
        db_spectrum = ((20*(numpy.log10(spectrum + 1e-30))).clip(-spec_range, 0.0) + spec_range)/spec_range
        energy = spectrum.sum()
        spectral_centroid = 0

        if energy > 1e-20:
            # calculate the spectral centroid
            if self.spectrum_range == None:
                self.spectrum_range = numpy.arange(length)

            spectral_centroid = (spectrum * self.spectrum_range).sum() / (energy * (length - 1)) * self.samplerate * 0.5
            # clip > log10 > scale between 0 and 1
            spectral_centroid = (math.log10(self.clip(spectral_centroid, self.lower, self.higher)) - self.lower_log) / (self.higher_log - self.lower_log)

        return (spectral_centroid, db_spectrum)


def interpolate_colors(colors, flat=False, num_colors=256):
    """ given a list of colors, create a larger list of colors interpolating
    the first one. If flatten is True a list of numers will be returned. If
    False, a list of (r,g,b) tuples. num_colors is the number of colors wanted
    in the final list """

    palette = []

    for i in range(num_colors):
        index = (i * (len(colors) - 1))/(num_colors - 1.0)
        index_int = int(index)
        alpha = index - float(index_int)

        if alpha > 0:
            r = (1.0 - alpha) * colors[index_int][0] + alpha * colors[index_int + 1][0]
            g = (1.0 - alpha) * colors[index_int][1] + alpha * colors[index_int + 1][1]
            b = (1.0 - alpha) * colors[index_int][2] + alpha * colors[index_int + 1][2]
        else:
            r = (1.0 - alpha) * colors[index_int][0]
            g = (1.0 - alpha) * colors[index_int][1]
            b = (1.0 - alpha) * colors[index_int][2]

        if flat:
            palette.extend((int(r), int(g), int(b)))
        else:
            palette.append((int(r), int(g), int(b)))

    return palette


class WaveformImage(object):

    def __init__(self, image_width, image_height, nframes, samplerate, buffer_size, bg_color=None, color_scheme=None, filename=None):

        self.image_width = image_width
        self.image_height = image_height
        self.nframes = nframes
        self.samplerate = samplerate
        self.fft_size = buffer_size
        self.buffer_size = buffer_size
        self.filename = filename

        self.bg_color = bg_color
        if not bg_color:
            self.bg_color = (0,0,0)
        self.color_scheme = color_scheme
        if not color_scheme:
            self.color_scheme = 'default'
        colors = color_schemes[self.color_scheme]['waveform']
        self.color_lookup = interpolate_colors(colors)

        self.samples_per_pixel = self.nframes / float(self.image_width)
        self.peaks_adapter = FixedSizeInputAdapter(int(self.samples_per_pixel), 1, pad=False)
        self.peaks_adapter_nframes = self.peaks_adapter.nframes(self.nframes)
        self.spectral_centroid = SpectralCentroid(self.fft_size, self.nframes, self.samplerate, numpy.hanning)

        self.image = Image.new("RGB", (self.image_width, self.image_height), self.bg_color)
        self.pixel = self.image.load()
        self.draw = ImageDraw.Draw(self.image)
        self.previous_x, self.previous_y = None, None
        self.frame_cursor = 0
        self.pixel_cursor = 0

    def peaks(self, samples):
        """ read all samples between start_seek and end_seek, then find the minimum and maximum peak
        in that range. Returns that pair in the order they were found. So if min was found first,
        it returns (min, max) else the other way around. """

        max_index = numpy.argmax(samples)
        max_value = samples[max_index]

        min_index = numpy.argmin(samples)
        min_value = samples[min_index]

        if min_index < max_index:
            return (min_value, max_value)
        else:
            return (max_value, min_value)

    def color_from_value(self, value):
        """ given a value between 0 and 1, return an (r,g,b) tuple """

        return ImageColor.getrgb("hsl(%d,%d%%,%d%%)" % (int( (1.0 - value) * 360 ), 80, 50))

    def draw_peaks(self, x, peaks, spectral_centroid):
        """ draw 2 peaks at x using the spectral_centroid for color """

        y1 = self.image_height * 0.5 - peaks[0] * (self.image_height - 4) * 0.5
        y2 = self.image_height * 0.5 - peaks[1] * (self.image_height - 4) * 0.5

        line_color = self.color_lookup[int(spectral_centroid*255.0)]

        if self.previous_y != None:
            self.draw.line([self.previous_x, self.previous_y, x, y1, x, y2], line_color)
        else:
            self.draw.line([x, y1, x, y2], line_color)

        self.previous_x, self.previous_y = x, y2

        self.draw_anti_aliased_pixels(x, y1, y2, line_color)

    def draw_anti_aliased_pixels(self, x, y1, y2, color):
        """ vertical anti-aliasing at y1 and y2 """

        y_max = max(y1, y2)
        y_max_int = int(y_max)
        alpha = y_max - y_max_int

        if alpha > 0.0 and alpha < 1.0 and y_max_int + 1 < self.image_height:
            current_pix = self.pixel[x, y_max_int + 1]

            r = int((1-alpha)*current_pix[0] + alpha*color[0])
            g = int((1-alpha)*current_pix[1] + alpha*color[1])
            b = int((1-alpha)*current_pix[2] + alpha*color[2])

            self.pixel[x, y_max_int + 1] = (r,g,b)

        y_min = min(y1, y2)
        y_min_int = int(y_min)
        alpha = 1.0 - (y_min - y_min_int)

        if alpha > 0.0 and alpha < 1.0 and y_min_int - 1 >= 0:
            current_pix = self.pixel[x, y_min_int - 1]

            r = int((1-alpha)*current_pix[0] + alpha*color[0])
            g = int((1-alpha)*current_pix[1] + alpha*color[1])
            b = int((1-alpha)*current_pix[2] + alpha*color[2])

            self.pixel[x, y_min_int - 1] = (r,g,b)

    def process(self, frames, eod):
        if len(frames) == 1:
            frames.shape = (len(frames),1)
            buffer = frames.copy()
        else:
            buffer = frames[:,0].copy()
            buffer.shape = (len(buffer),1)

        for samples, end in self.peaks_adapter.process(buffer, eod):
            if self.pixel_cursor == self.image_width:
                break
            peaks = self.peaks(samples)
            # FIXME : too much repeated spectrum computation cycle
            (spectral_centroid, db_spectrum) = self.spectral_centroid.process(buffer, True)
            self.draw_peaks(self.pixel_cursor, peaks, spectral_centroid)
            self.pixel_cursor += 1

    def save(self):
        a = 25
        for x in range(self.image_width):
            self.pixel[x, self.image_height/2] = tuple(map(lambda p: p+a, self.pixel[x, self.image_height/2]))
        self.image.save(self.filename)


class SpectrogramImage(object):
    def __init__(self, image_width, image_height, fft_size, bg_color = None, color_scheme = None):

        #FIXME: bg_color is ignored

        if not color_scheme:
            color_scheme = 'default'

        self.image = Image.new("P", (image_height, image_width))

        self.image_width = image_width
        self.image_height = image_height
        self.fft_size = fft_size

        colors = color_schemes[color_scheme]['spectrogram']

        self.image.putpalette(interpolate_colors(colors, True))

        # generate the lookup which translates y-coordinate to fft-bin
        self.y_to_bin = []
        f_min = 100.0
        f_max = 22050.0
        y_min = math.log10(f_min)
        y_max = math.log10(f_max)
        for y in range(self.image_height):
            freq = math.pow(10.0, y_min + y / (image_height - 1.0) *(y_max - y_min))
            bin = freq / 22050.0 * (self.fft_size/2 + 1)

            if bin < self.fft_size/2:
                alpha = bin - int(bin)

                self.y_to_bin.append((int(bin), alpha * 255))

        # this is a bit strange, but using image.load()[x,y] = ... is
        # a lot slower than using image.putadata and then rotating the image
        # so we store all the pixels in an array and then create the image when saving
        self.pixels = []

    def draw_spectrum(self, x, spectrum):
        for (index, alpha) in self.y_to_bin:
            self.pixels.append( int( ((255.0-alpha) * spectrum[index] + alpha * spectrum[index + 1] )) )

        for y in range(len(self.y_to_bin), self.image_height):
            self.pixels.append(0)

    def save(self, filename):
        self.image.putdata(self.pixels)
        self.image.transpose(Image.ROTATE_90).save(filename)


def create_spectrogram_png(input_filename, output_filename_s, image_width, image_height, fft_size,
                           bg_color = None, color_scheme = None):
    audio_file = audiolab.sndfile(input_filename, 'read')

    samples_per_pixel = audio_file.get_nframes() / float(image_width)
    processor = AudioProcessor(audio_file, fft_size, numpy.hanning)

    spectrogram = SpectrogramImage(image_width, image_height, fft_size, bg_color, color_scheme)

    for x in range(image_width):

        if x % (image_width/10) == 0:
            sys.stdout.write('.')
            sys.stdout.flush()

        seek_point = int(x * samples_per_pixel)
        next_seek_point = int((x + 1) * samples_per_pixel)
        (spectral_centroid, db_spectrum) = processor.spectral_centroid(seek_point)
        spectrogram.draw_spectrum(x, db_spectrum)

    spectrogram.save(output_filename_s)

    print " done"


class Noise(object):
    """A class that mimics audiolab.sndfile but generates noise instead of reading
    a wave file. Additionally it can be told to have a "broken" header and thus crashing
    in the middle of the file. Also useful for testing ultra-short files of 20 samples."""
    def __init__(self, num_frames, has_broken_header=False):
        self.seekpoint = 0
        self.num_frames = num_frames
        self.has_broken_header = has_broken_header

    def seek(self, seekpoint):
        self.seekpoint = seekpoint

    def get_nframes(self):
        return self.num_frames

    def get_samplerate(self):
        return 44100

    def get_channels(self):
        return 1

    def read_frames(self, frames_to_read):
        if self.has_broken_header and self.seekpoint + frames_to_read > self.num_frames / 2:
            raise IOError()

        num_frames_left = self.num_frames - self.seekpoint
        if num_frames_left < frames_to_read:
            will_read = num_frames_left
        else:
            will_read = frames_to_read
        self.seekpoint += will_read
        return numpy.random.random(will_read)*2 - 1
