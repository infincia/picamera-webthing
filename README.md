# picamera-webthing

A Web Thing compatible Pi Camera server for Mozilla IoT Gateway

**Note:** using this with the Mozilla IoT gateway requires some minor changes
that are not included in the public Gateway releases at the moment. For testing,
check out the `images` branch of my fork of the Gateway [here](https://github.com/infincia/gateway/tree/images). Fair warning, that branch
is being force-pushed frequently at the moment.

## Screenshots (of the Gateway, `picamera-webthing` itself has no UI)

![Gateway with Pi Camera Thing added](/screenshots/gateway.jpg?raw=true "Gateway with Pi Camera Thing added")

## Features

* Makes live still images available through the Web Thing API
* Compatible with the Mozilla IoT Gateway (see note above)
    * No special adapter required (just the generic `thing-url` adapter)
* Compatible with all Raspberry Pi models with a Pi Camera connector
* Compatible with all Pi Camera modules that work with the Python `picamera` library
* Support for si7021 temperature/humidity sensors
    * Temperature and humidity are exposed as Thing properties
    * Other sensors can be added easily
* Configuration file support for:
    * Web Thing name
    * HTTP port to listen on (8080 by default)
    * Update interval for sensor readings
    * Camera settings
        * Frame rate
        * Resolution
        * Rotation
        * Shutter speed
        * Sensor mode
        * Exposure mode
        * ISO
* Support for changing* some camera settings live from the Gateway interface
    * Frame rate
    * Resolution
    * Exposure mode

* Note that these are not persistent yet, restarting the server resets them. If
you want a setting to be persistently saved at the moment, edit the config
file (see below).

## Details

Uses the [`webthing-python`](https://github.com/mozilla-iot/webthing-python) library along with the `picamera` library. Makes some
of the camera settings available as Web Thing properties, along with a periodically
updated still image.

It does not provide for displaying full motion video yet, but it is possible
and I've done some initial work toward adding support for it.

There are advantages and tradeoffs to using the Pi Camera this way, making it
more suitable for some use cases than others.

For example, weather cameras generally don't need to see *motion* as much as
they simply need to *see* in detail.

### Still Images vs Video

At the moment, `picamera-webthing` is designed to use still images and not
video. This is primarily for simplicity, as still images are much easier to
distribute over the network *reliably*.

As an example, the current design will work properly whether you're accessing the
Gateway locally or remotely (via your *.mozilla-iot.org url), as long as you can
access the Gateway interface the camera images will show up.

Using the Pi Camera sensor in "video mode" also adds a noticeable amount of noise
to the image, and puts some significant limits on what the camera can actually see.

However, with the sensor in "still image" mode, and exposure mode set to "night",
the Pi Camera module is capable of taking *very* high resolution (3280 x 2464)
images in low light conditions (i.e. without a separate infrared illuminator) with
very low noise.

### Binary vs base64

The Gateway uses JSON to communicate with Web Things, making base64 encoded
JPEG images the path of least resistance to get things working quickly.

This has some overhead, but it works well. If at some point the Gateway adds
supports for binary communication, it will be trivial to switch away from base64.

## Web Thing properties

The resolution, frame rate, and exposure mode settings of the Pi Camera module are
exposed as Web Thing properties, and can be set live while the camera is in use.

You will probably need to adjust them to see what works best for your use case,
but be aware that turning the resolution and/or frame rate up will require a lot
of bandwidth. The Gateway, browser, or network connection may not be able to
handle it.

Note that some combinations of settings will not achieve what you may be hoping
for, or may not make sense. For example setting the exposure mode to "night" and
then setting the frame rate to 10fps is likely to result in a completely black
picture unless there is a lot of light available (see below for how night mode
actually works, then it'll make sense).

### Resolution

Adjusts the captured JPEG image resolution. The display size in the Gateway is
(mostly) unrelated to the actual image resolution.

This directly affects the bandwidth required, so don't set it to a high resolution
unless you really need to.

The default is `800x600`, which is about halfway between DVD and 720p quality and
quite high for most purposes, particularly if viewed in the Gateway rather than
full screen.

At the default 800x600 resolution, updating the image every ~3 seconds uses about
106Kbps of bandwidth (for comparison, ADSL upstream tends to be 512-1024kbps).

You'll notice that the default resolution is square rather than wide screen, this
is intentional: **the only thing the widescreen resolutions do on a Pi Camera is
cut off the top and bottom of the sensor area**. They aren't actually *wider*,
just "letterboxed".

### Frame rate

By default a new image is *requested* from the Pi Camera module every `(1.0 / framerate)`
seconds, but this does not factor in the time it takes the Pi Camera to actually
capture an image, which in some cases can be significant.

This is particularly true in "night" exposure mode with the shutter speed set
dynamically by the camera firmware (see below).

So just be aware that setting the frame rate higher will not always cause the
image to be updated that fast, as the other camera settings must allow the sensor
to actually capture images that fast as well.

This code may also need some minor changes to support rapid image capture. At the
moment 3-4fps is the maximum rate in practice, higher rates will still
end up waiting at least 200-250ms for each capture.

### Exposure mode

For more detailed explanations, refer to the [picamera documentation](https://picamera.readthedocs.io).

#### Auto

This is the default. In this mode, the Pi Camera firmware will adjust some of
the other settings to take reasonably good pictures.

It will dynamically change the frame rate in low light, but only to a point;
auto mode will not drop the frame rate as far down as the "night" exposure mode
will, so it doesn't work as well as night mode in very low light.

#### Night

In night mode, the camera firmware will prefer using longer shutter times to gather
enough light, instead of turning up the sensor gain level (which would increase
image noise).

Depending on how much light is available, in night mode the shutter speed can
drop all the way down to 0.1 - 0.15fps (6-10 seconds *per frame*). This can make
a significant difference, so if you find your images looks dark even with an
infrared illuminator in use, turn on night mode.

This is one of the reasons streaming video is not *always* preferable, at that
frame rate it may as well be taking still images anyway.

## Installation

At the moment you should install and run the `picamera-webthing` service as root.

This makes it easier to get things running but is definitely not ideal. It should
be possible to run as a normal user, but that requires ensuring that the server
can access anything it needs to without requiring root (this is not the case for
i2c sensors by default).

### Step by step

```
First, become root if you aren't already:

```
sudo -s
```

Then clone this repository:

```
cd /opt
git clone https://github.com/infincia/picamera-webthing.git
```

Install the dependencies:

```
cd /opt/picamera-webthing
apt install python3-pip
pip3 install --user -r requirements.txt
```

This *will not* alter or conflict with any Python packages installed via `apt`,
they will be installed in `/root/.local/`.

The server will work without a config file, but you may want to alter some of
the settings, such as the Web Thing name (particularly if you have more than
one, the default will be 'picam1' for all of them until you change it):

```
mkdir /var/lib/picamera-webthing
cp /opt/picamera-webthing/defaults.toml /var/lib/picamera-webthing/config.toml
```

You can edit the `/var/lib/picamera-webthing/config.toml` file directly. In the
near future the camera settings you change in the Gateway interface will be saved
automatically.


Now you can enable and start the service:

```
systemctl enable /opt/picamera-webthing/picamera-webthing.service
systemctl start picamera-webthing
```

You should be able to discover the new Pi Camera Web Thing in the Mozilla
IoT Gateway, as long as the `thing-url` adapter is enabled.
