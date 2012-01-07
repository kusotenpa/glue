#!/usr/bin/env python
import re
import os
import sys
import copy
import hashlib
import subprocess
import ConfigParser
from optparse import OptionParser, OptionGroup

from PIL import Image as PImage


TRANSPARENT = (255, 255, 255, 0)


class MultipleImagesWithSameNameError(Exception):
    """Raised if two images are going to have the same css class name."""
    pass


class SourceImagesNotFoundError(Exception):
    """Raised if one folder doesn't contain any valid image."""
    pass


class NoSpritesFoldersFoundError(Exception):
    """Raised if there is not any valid sprites folder."""
    pass


class InvalidImageOrderingAlgorithmError(Exception):
    """Raised if the ordering algorithm is invalid."""
    pass


class Node(object):

    def __init__(self, x=0, y=0, width=0, height=0, used=False,
                 down=None, right=None):
        """Node constructor.

        :param x: X coordinates.
        :param y: Y coordinates.
        :param width: Image width.
        :param height: Image height.
        :param used: Flag to determine if the node is used.
        :param down: Down :class:`~Node`.
        :param right Right :class:`~Node`.
        """
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.used = used
        self.right = right
        self.down = down

    def find(self, node, width, height):
        """Find a node to allocate this image size.

        :param node: The node to search in.
        :param width: The amount of pixel to grow down (width).
        :param height: The amount of pixel to grow down (height).
        """
        if node.used:
            return self.find(node.right, width, height) or \
                   self.find(node.down, width, height)
        elif node.width >= width and node.height >= height:
            return node
        return None

    def grow(self, width, height):
        """ Grow the canvas to the more appropriate direction.

        :param width: Amount of pixel to grow down (width).
        :param height: Amount of pixel to grow down (height).
        """
        can_grow_d = width <= self.width
        can_grow_r = height <= self.height

        should_grow_r = can_grow_r and self.height >= (self.width + width)
        should_grow_d = can_grow_d and self.width >= (self.height + height)

        if should_grow_r:
            return self.grow_right(width, height)
        elif should_grow_d:
            return self.grow_down(width, height)
        elif can_grow_r:
            return self.grow_right(width, height)
        elif can_grow_d:
            return self.grow_down(width, height)

        return None

    def grow_right(self, width, height):
        """Grow the canvas to the right.

        :param width: The amount of pixel to grow down (width).
        :param height: The amount of pixel to grow down (height).
        """
        old_self = copy.copy(self)
        self.used = True
        self.x = self.y = 0
        self.width += width
        self.down = old_self
        self.right = Node(x=old_self.width,
                          y=0,
                          width=width,
                          height=self.height)

        node = self.find(self, width, height)
        if node:
            return self.split(node, width, height)
        return None

    def grow_down(self, width, height):
        """Grow the canvas down.

        :param width: The amount of pixel to grow down (width).
        :param height: The amount of pixel to grow down (height).
        """
        old_self = copy.copy(self)
        self.used = True
        self.x = self.y = 0
        self.height += height
        self.right = old_self
        self.down = Node(x=0,
                         y=old_self.height,
                         width=self.width,
                         height=height)

        node = self.find(self, width, height)
        if node:
            return self.split(node, width, height)
        return None

    def split(self, node, width, height):
        """Split the node to allocate a new one of this size.

        :param node: The node to be splited.
        :param width: The new node width.
        :param height: The new node height.
        """
        node.used = True
        node.down = Node(x=node.x,
                               y=node.y + height,
                               width=node.width,
                               height=node.height - height)
        node.right = Node(x=node.x + width,
                                y=node.y,
                                width=node.width - width,
                                height=height)
        return node


class Image(object):

    ORDERINGS = ['maxside', 'width', 'height', 'area']

    def __init__(self, name, sprite):
        """ Image constructor

        :param name: Image name.
        :param sprite: :class:`~Sprite` instance for this image."""
        self.name = name
        self.sprite = sprite
        self.filename, self.format = name.rsplit('.', 1)
        image_path = os.path.join(sprite.path, name)

        image_file = open(image_path, "rb")
        self.image = PImage.open(image_file)
        self.image.load()
        image_file.close()

        if self.sprite.get_conf('crop'):
            self._crop_image()

        self.width, self.height = self.image.size

        self.width += self.padding[1] + self.padding[3]
        self.height += self.padding[0] + self.padding[2]
        self.node = None

    def _crop_image(self):
        """Crop the image searching for the smallest possible bounding box
        without lossing any non-alpha pixel.

        This crop is only used if the crop preference is present.
        """
        width, height = self.image.size
        maxx = maxy = 0
        minx = miny = sys.maxint

        for x in xrange(width):
            for y in xrange(height):
                if y > miny and y < maxy and maxx == x:
                    continue
                if self.image.getpixel((x, y)) != TRANSPARENT:
                    if x < minx:
                        minx = x
                    if x > maxx:
                        maxx = x
                    if y < miny:
                        miny = y
                    if y > maxy:
                        maxy = y
        self.image = self.image.crop((minx, miny, maxx + 1, maxy + 1))

    def _generate_padding(self, padding):
        """Return a four element list with the desired padding.

        :param padding: The padding as a list or a raw string representing
                        the padding for this image."""

        if type(padding) == str:
            padding = padding.replace('px', '').split()

        if len(padding) == 3:
            padding = padding + [padding[1]]
        elif len(padding) == 2:
            padding = padding * 2
        elif len(padding) == 1:
            padding = padding * 4
        elif len(padding) == 0:
            padding = [self.DEFAULT_PADDING] * 4
        return map(int, padding)

    @property
    def class_name(self):
        """Return the css class name for this file.

        This css class name will have the following format:

        ``.[namespace]-[sprite_name]-[image_name]{ ... }``

        The image_name will only contain the alphanumeric characters,
        ``-`` and ``_``. The default namespace is ``sprite``, it but could
        be overrided using the ``--namespace`` optional argument.


        * ``animals/cat.png`` css class will be ``.sprite-animals-cat``
        * ``animals/cow-20.png`` css class will be ``.sprite-animals-cow``
        """
        name = self.filename
        if not self.sprite.manager.options.ignore_filename_paddings:
            padding_info_name = '-'.join(self._padding_info)
            if padding_info_name:
                padding_info_name = '_%s' % padding_info_name
            name = name[:len(padding_info_name) * -1 or None]
        name = re.sub(r'[^\w\-\_]', '', name)
        return '%s-%s' % (self.sprite.namespace, name)

    @property
    def _padding_info(self):
        """Return the padding information from the filename. """
        padding_info = self.filename.rsplit('_', 1)[-1]
        if re.match(r"^(\d+-?){,4}\d+$", padding_info):
            return padding_info.split('-')
        return []

    @property
    def padding(self):
        """Return the padding for this image based on the filename and
        sprite settings file preferences.

        * ``filename.png`` will have the default padding ``10px``.
        * ``filename-20.png`` -> ``20px`` all arround the image.
        * ``filename-1-2-3.png`` -> ``1px 2px 3px 2px`` arround the image.
        * ``filename-1-2-3-4.png`` -> ``1px 2px 3px 4px`` arround the image.

        """
        padding = self._padding_info
        if len(padding) == 0 or \
           self.sprite.manager.options.ignore_filename_paddings:
            padding = self.sprite.get_conf('padding')
        return self._generate_padding(padding)

    @property
    def x(self):
        """The y coordinate for this image."""
        return self.node.x + self.padding[3]

    @property
    def y(self):
        """The x coordinate for this image."""
        return self.node.y + self.padding[0]

    def __lt__(self, img):
        """Use the maxside, width, height or area as ordering algorithm.

        :param img: Another :class:`~Image`."""
        algorithm = self.sprite.get_conf('algorithm')
        if algorithm == 'width':
            return self.width <= img.width
        elif algorithm == 'height':
            return self.height <= img.height
        elif algorithm == 'area':
            return self.width * self.height <= img.width * img.height
        else:
            return max(self.width, self.height) <= max(img.width, img.height)


class Sprite(object):

    DEFAULT_SETTINGS = {'padding': '0',
                        'algorithm': 'maxside',
                        'namespace': 'sprite',
                        'crop': False,
                        'url': ''}

    def __init__(self, name, path, manager):
        """Sprite constructor.

        :param name: The sprite name.
        :param path: The sprite path
        :param manager: The sprite manager. :class:`~MultipleSpriteManager` or
                        :class:`SimpleSpriteManager`"""
        self.name = name
        self.manager = manager
        self.images = []
        self.path = path

        algorithm = self.get_conf('algorithm')
        if algorithm not in Image.ORDERINGS:
            raise InvalidImageOrderingAlgorithmError(algorithm)

        self.process()

    def process(self):
        """Process a sprite path searchig for all the images and then
        allocate all of them in the more appropriate position.
        """
        self.images = self._locate_images()
        width = self.images[0].width
        height = self.images[0].height
        root = Node(width=width, height=height)

        # Loot all over the images creating a binary tree
        for image in self.images:
            print "\t %s => .%s" % (image.name, image.class_name)
            node = root.find(root, image.width, image.height)
            if node:  # Use this node
                image.node = root.split(node, image.width, image.height)
            else:  # Grow the canvas
                image.node = root.grow(image.width, image.height)

    def _locate_images(self):
        """Return all the valid images within a folder.

        All the files with an extension not included in VALID_IMAGE_EXTENSIONS
        (png, jpg, jpeg and gif) or begging with a '.' will be ignored.

        If the folder doesn't contain any valid image it will raise
        a :class:`~MultipleImagesWithSameNameError`

        The list of images will be ordered using the desired ordering
        algorithm. The default one is 'maxside'.
        """
        extensions = '|'.join(self.manager.VALID_IMAGE_EXTENSIONS)
        extension_re = re.compile('.+\.(%s)$' % extensions, re.IGNORECASE)

        images = [Image(n, sprite=self) for n in os.listdir(self.path) if \
                                    not n.startswith('.') and \
                                    extension_re.match(n)]

        if len(images) == 0:
            raise SourceImagesNotFoundError()

        # Check if there are duplicate class names
        class_names = [i.class_name for i in images]
        if len(set(class_names)) != len(images):
            dup = [i for i in images if class_names.count(i.class_name) > 1]
            raise MultipleImagesWithSameNameError(dup)

        return sorted(images, reverse=True)

    def save_image(self):
        """Create the image file for this sprite."""
        print green("Generating '%s' image file..." % self.name)

        sprite_output_path = self.manager.output_path('img')
        # Search for the max x and y neccesary to generate the canvas.
        width = height = 0

        for image in self.images:
            padding = image.padding
            x = image.node.x + image.width + padding[1] + padding[3]
            y = image.node.y + image.height + padding[0] + padding[2]
            if width < x:
                width = x
            if height < y:
                height = y

        # Create the sprite canvas
        canvas = PImage.new('RGBA', (width, height), (0, 0, 0, 0))

        # Paste the images inside the canvas
        for image in self.images:
            canvas.paste(image.image, (image.x, image.y))

        # Save png
        sprite_filename = '%s.png' % self.filename
        sprite_image_path = os.path.join(sprite_output_path, sprite_filename)
        save = lambda: canvas.save(sprite_image_path, optimize=True)
        save()

        if self.manager.options.optipng:
            print green("Optimizing '%s' using optipng..." % self.name)
            command = ["%s %s" % (self.manager.options.optipngpath,
                                  sprite_image_path)]

            error = subprocess.call(command, shell=True, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
            if error:
                print red("Error: optipng has fail, reverting to the "
                          "original file.")
                save()

    def save_css(self):
        """Create the css file for this sprite."""
        print green("Generating '%s' css file..." % self.name)

        # Generate css files
        output_path = self.manager.output_path('css')
        format = 'less' if self.manager.options.less else 'css'
        css_filename = os.path.join(output_path, '%s.%s' % (self.filename,
                                                            format))
        css_file = open(css_filename, 'w')

        # Create all the necessary class names
        for image in self.images:
            data = {'namespace': image.sprite.namespace,
                    'sprite_url': image.sprite.image_url,
                    'image_class_name': image.class_name,
                    'top': image.node.y * -1 if image.node.y else 0,
                    'left': image.node.x * -1 if image.node.x else 0,
                    'width': image.width,
                    'height': image.height}

            css_file.write((".%(image_class_name)s{ "
                          "background:url('%(sprite_url)s') no-repeat "
                          "%(left)ipx %(top)ipx;"
                          "width:%(width)spx; height:%(height)spx;}\n") % data)
        css_file.close()

    @property
    def namespace(self):
        """Return the namespace for this sprite."""
        return '%s-%s' % (self.get_conf('namespace'), self.name)

    @property
    def filename(self):
        """Return the desired filename for this sprite generated files."""
        return self.name

    @property
    def image_path(self):
        """Return the output path for the image file."""
        return os.path.join(self.manager.output_path('img'),
                            '%s.png' % self.filename)

    @property
    def image_url(self):
        """Return the sprite image url."""
        url = os.path.relpath(self.image_path, self.manager.output_path('css'))
        if self.get_conf('url'):
            url = os.path.join(self.get_conf('url'), '%s.png' % self.filename)

        if self.manager.options.cachebuster:
            sprite_file = open(self.image_path, 'rb')
            sprite_hash = hashlib.sha1(sprite_file.read()).hexdigest()
            sprite_file.close()
            url = "%s?%s" % (url, sprite_hash[:6])
        return url

    @property
    def config(self):
        """Return a ConfigParser instance with this sprite preferences."""
        if not getattr(self, '_config', None):
            self._config = ConfigParser.RawConfigParser(self.DEFAULT_SETTINGS)
            self._config.read(os.path.join(self.path, 'sprite.conf'))
        return self._config

    def get_conf(self, name):
        """Return the desired preference for this sprite. If the preference
        was overrided from the command line, use that value, else use the
        settings file. If neither the file or command line sets that property,
        return the default value.

        :param name: The preference name."""
        try:
            value = getattr(self.manager.options, name, None) or \
               self.config.get('defaults', name)
        except ConfigParser.NoSectionError:
            value = self.DEFAULT_SETTINGS.get(name)

        return value


class BaseManager(object):

    VALID_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif']

    def __init__(self, path, options, output=None):
        """ BaseManager constructor.

        :param path: Sprite path.
        :param name: OptionParser instance with all the preferences for this
                     sprite.
        """
        self.path = path
        self.options = options
        self.output = output
        self.sprites = []

    def process_sprite(self, path, name):
        """Create a new Sprite using this path and name and append it to the
        sprites list.

        :param path: Sprite path.
        :param name: Sprite name.
        """
        print cyan("Processing '%s':" % name)
        sprite = Sprite(name=name, path=path, manager=self)
        self.sprites.append(sprite)

    def save(self):
        """Save all the sprites inside this manager."""
        for sprite in self.sprites:
            sprite.save_image()
            sprite.save_css()

    def output_path(self, format):
        """Return the path where all the generated files will be saved.

        :param name: Sprite name
        """
        if format == 'css' and self.options.css_dir:
            sprite_output_path = self.options.css_dir
        elif format == 'img' and self.options.img_dir:
            sprite_output_path = self.options.img_dir
        else:
            sprite_output_path = self.output
        if not os.path.exists(sprite_output_path):
            os.makedirs(sprite_output_path)
        return sprite_output_path

    def process(self):
        raise NotImplementedError()


class MultipleSpriteManager(BaseManager):

    def process(self):
        """Process a path searching for folders that contains images.
        Every folder will be a new sprite with all the images inside.

        The filename of the image also can contain information about the
        padding needed arround the image.

        * ``filename.png`` wil have the default padding (10px).
        * ``filename_20.png`` will have 20px all arround the image.
        * ``filename_1-2-3.png`` will have 1px 2px 3px 2px arround the image.
        * ``filename_1-2-3-4.png`` will have 1px 2px 3px 4px arround the image.

        The generated css file will have a css class for every image found
        inside the sprite folder. This css class names will have the
        following format:

        ``.[namespace]-[sprite_name]-[image_name]{ ... }``

        The image_name will only contain the alphanumeric characters,
        ``-`` and ``_``. The default namespace is ``sprite``, it but could be
        overrided using the ``--namespace`` optional argument.


        * ``animals/cat.png`` css class will be ``.sprite-animals-cat``
        * ``animals/cow-20.png`` css class will be ``.sprite-animals-cow``

        If two images has the same name, a
        :class:`~MultipleImagesWithSameNameError` error will be raised.
        """
        for sprite_name in os.listdir(self.path):
            # Only process folders
            path = os.path.join(self.path, sprite_name)
            if os.path.isdir(path):
                self.process_sprite(path=path, name=sprite_name)

        if len(self.sprites) == 0:
            raise NoSpritesFoldersFoundError()

        self.save()


class SimpleSpriteManager(BaseManager):

    def process(self):
        """Process an unique folder and create one sprite. It works in the
        same way than :class:`~MultipleSpriteManager` but for only one folder.

        This is not the default manager. It is only used if you use
        the ``--simple`` default argument.
        """
        self.process_sprite(path=self.path, name=os.path.basename(self.path))
        self.save()


def command_exists(command):
    """ Check if a command exists running it."""
    try:
        subprocess.check_call([command], shell=True, stdin=subprocess.PIPE,
                              stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    except subprocess.CalledProcessError:
        return False
    return True


def _wrap_with(code):
    """ Function for wrap strings in ANSI color codes.
    Copy & Pasted from fabric.
    """
    def inner(text, bold=False):
        c = code
        if bold:
            c = "1;%s" % c
        return "\033[%sm%s\033[0m" % (c, text)
    return inner

red = _wrap_with('31')
green = _wrap_with('32')
yellow = _wrap_with('33')
blue = _wrap_with('34')
magenta = _wrap_with('35')
cyan = _wrap_with('36')
white = _wrap_with('37')

#########################################################################
# PIL currently doesn't support full alpha for PNG8 so it's necessary to
# monkey patch PIL to support them.
# http://mail.python.org/pipermail/image-sig/2010-October/006533.html
#########################################################################
from PIL import ImageFile, PngImagePlugin


def patched_chunk_tRNS(self, pos, len):
    i16 = PngImagePlugin.i16
    s = ImageFile._safe_read(self.fp, len)
    if self.im_mode == "P":
        self.im_info["transparency"] = map(ord, s)
    elif self.im_mode == "L":
        self.im_info["transparency"] = i16(s)
    elif self.im_mode == "RGB":
        self.im_info["transparency"] = i16(s), i16(s[2:]), i16(s[4:])
    return s
PngImagePlugin.PngStream.chunk_tRNS = patched_chunk_tRNS


def patched_load(self):
    if self.im and self.palette and self.palette.dirty:
        apply(self.im.putpalette, self.palette.getdata())
        self.palette.dirty = 0
        self.palette.rawmode = None
        try:
            trans = self.info["transparency"]
        except KeyError:
            self.palette.mode = "RGB"
        else:
            try:
                for i, a in enumerate(trans):
                    self.im.putpalettealpha(i, a)
            except TypeError:
                self.im.putpalettealpha(trans, 0)
            self.palette.mode = "RGBA"
    if self.im:
        return self.im.pixel_access(self.readonly)
PImage.Image.load = patched_load
#########################################################################


def main():
    parser = OptionParser(usage="usage: %prog [options] dir [output]")
    parser.add_option("-s", "--simple", action="store_true", dest="simple",
                      help="Only generate sprites for one folder.")
    parser.add_option("-c", "--crop", dest="crop", action='store_true',
                help="Crop images removing unnecessary transparent margins.")
    parser.add_option("-l", "--less", dest="less", action='store_true',
                help="The output stylesheets will be .less and not .css .")
    parser.add_option("-u", "--url", dest="url", default=None,
                      help="Prepend this url to the sprites filename.")

    group = OptionGroup(parser, "Output Options")
    group.add_option("--css", dest="css_dir", default='',
                    help="Output directory for the css files.")
    group.add_option("--img", dest="img_dir", default='',
                    help="Output directory for the img files.")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Advanced Options")
    group.add_option("-a", "--algorithm", dest="algorithm", default=None,
                    help=("Ordering algorithm: maxside, width, height or "
                          "area (default: maxside)."))
    group.add_option("--namespace", dest="namespace",  default=None,
                      help="Namespace for the css (default: sprite).")
    group.add_option("--ignore-filename-paddings",
                      dest="ignore_filename_paddings", action='store_true',
                      help="Ignore filename paddings.", default=False)
    parser.add_option_group(group)

    group = OptionGroup(parser, "Optipng Options",
                        "You must install optipng before using this options.")
    group.add_option("--optipng", dest="optipng", action='store_true',
                help="Postprocess images using optipng.")
    group.add_option("--optipngpath", dest="optipngpath", default='optipng',
                    help="Path to optipng (default: optipng).")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Browser Cache Invalidation Options")
    group.add_option("--cachebuster", dest="cachebuster",
                    action='store_true',
                    help=("Use the sprite's sha1 6 first characters as a "
                          "queryarg everywhere that file is used on the "
                          "css."))
    parser.add_option_group(group)

    (options, args) = parser.parse_args()

    if len(args) == 0:
        parser.error("You must choose the folder that contains the sprites.")

    if len(args) == 1 and not (options.css_dir and options.img_dir):
        parser.error(("You must choose the output folder using the output "
                      "argument or --img and --css."))

    if len(args) == 2 and (options.css_dir and options.img_dir):
        parser.error(("You must choose between using an unique output dir or "
                      "using --css and --img."))

    if options.optipng and not command_exists(options.optipngpath):
        parser.error("'optipng' seems to not be available. You must "
                     "install it before using the --optipng option or "
                     "provide a path using --optipngpath.")

    source = os.path.abspath(args[0])
    output = os.path.abspath(args[1]) if len(args) == 2 else None

    if options.simple:
        manager_cls = SimpleSpriteManager
    else:
        manager_cls = MultipleSpriteManager

    manager = manager_cls(path=source, output=output, options=options)

    try:
        manager.process()
    except MultipleImagesWithSameNameError, e:
        print
        print red("Conflict: Some images will have the same class name:")
        for image in e.args[0]:
            print '\t %s => .%s' % (image.name, image.class_name)
    except SourceImagesNotFoundError, e:
        print
        print red("Error: No images found.")
    except NoSpritesFoldersFoundError, e:
        print
        print red("Error: No sprites folders found.")


if __name__ == "__main__":
    main()