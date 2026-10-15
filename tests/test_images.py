import base64
from pathlib import Path
import tempfile
import unittest

from adseg_auto.images import descriptor, difference
from adseg_auto.input import Frame, Source
from adseg_auto.providers import _image_url
from adseg_auto.candidates import generate
from adseg_auto.pipeline import Config
from adseg_auto.util import file_hash


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adseg-image-tests-")
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_png_http_conversion_round_trips_descriptor(self):
        ppm = self.base / "frame.ppm"
        ppm.write_bytes(b"P6\n# test comment\n2 2\n255\n" + bytes((10,30,10,255,10,20,50,100,200,0,90,200)))
        frame = Frame("f",0,0,ppm,file_hash(ppm))
        url = _image_url(frame)
        png = self.base / "frame.png"
        png.write_bytes(base64.b64decode(url.split(",",1)[1]))
        self.assertEqual(descriptor(ppm),descriptor(png))
        self.assertEqual(difference(descriptor(ppm),descriptor(png)),0)

    def test_malformed_image_remains_a_hash_change_proposal(self):
        source = Source("s","synthetic",1,0,{},self.base)
        for i, content in enumerate((b"not an image", b"\x89PNG\r\n\x1a\nmalformed")):
            path = self.base / ("bad%d.png" % i)
            path.write_bytes(content)
            source.frames.append(Frame("f%d" % i,i*400000,i*0.4,path,file_hash(path)))
        candidates = generate(source,Config())
        self.assertEqual(len(candidates),1)
        self.assertIn("image_hash_change_fallback",candidates[0].cues)


if __name__ == "__main__":
    unittest.main()
