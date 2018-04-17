# -*- coding: utf-8 -*-

#!/usr/bin/env python3

import io
import os
import time
import uuid
import sys
import platform
import base64
import threading
import datetime
import functools
import logging

import tornado
import anyconfig
from webthing import Property, Thing, Value, WebThingServer
import picamera
import Adafruit_PureIO.smbus as smbus

print = functools.partial(print, flush = True)

logging.basicConfig(level = logging.DEBUG)
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "defaults.toml")

CONFIG_DIR = "/var/lib/picamera-webthing"
USER_CONFIG = os.path.join(CONFIG_DIR, "config.toml")

class PiCameraWebThing:
    """A Web Thing enabled raspberry pi camera"""

    def __init__(self):
        self.conf = anyconfig.load([DEFAULT_CONFIG, USER_CONFIG], ignore_missing = True, ac_merge = anyconfig.MS_REPLACE)
        self.ioloop = tornado.ioloop.IOLoop.current()

        self.device_name = self.conf['name']
        self.port = self.conf['port']

        self.si7021_enabled = self.conf['si7021']['enabled']
        self.sensors_update_interval = self.conf['sensors']['update_interval']

        self.use_video_port = self.conf['camera']['use_video_port']
        self.framerate = self.conf['camera']['framerate']
        self.iso = self.conf['camera']['iso']
        self.rotation = self.conf['camera']['rotation']
        self.shutter_speed = self.conf['camera']['shutter_speed']
        self.sensor_mode = self.conf['camera']['sensor_mode']
        self.exposure_mode = self.conf['camera']['exposure_mode']
        self.resolution = self.conf['camera']['resolution']

        """
            Only 3 camera settings are exposed as Thing properties, to avoid
            creating a clutter of unnecessary Detail bubbles in the Gateway
            interface
        """
        self.resolution_value = Value(self.resolution, lambda resolution: self.set_resolution(resolution))
        self.framerate_value = Value(self.framerate, lambda framerate: self.set_framerate(framerate))
        self.exposure_mode_value = Value(self.exposure_mode, lambda mode: self.set_exposure_mode(mode))
        self.base64_still_image_value = Value("")
        self.temperature_value = Value(0.0)
        self.humidity_value = Value(0.0)

        self.resolution_property = None
        self.framerate_property = None
        self.exposure_mode_property = None
        self.base64_still_image_property = None
        self.temperature_property = None
        self.humidity_property = None

        logger.info('Starting PiCamera Web Thing: %s', self.device_name)

        self.sensor_setup()
        self.camera_setup()
        self.webthing_setup()

    def start(self):
        self.server.start()

    def stop(self):
        self.server.stop()

    def cleanup(self):
        self.camera.stop_preview()
        self.camera.close()

    def camera_setup(self):
        """
            Starts a background thread for handling camera captures
        """
        self.camera = picamera.PiCamera()
        self.camera_lock = threading.Lock()

        with self.camera_lock:
            self.camera.resolution = self.resolution
            self.camera.rotation = self.rotation
            self.camera.iso = self.iso
            """
                We set the framerate to 30.0 at startup so the firmware has at
                least 90 frames (30 * 3 seconds) to use for calibrating the sensor,
                which is critical in low light. May need to do this periodically
                as well; if the framerate is set very low the camera will take
                several minutes or longer to react to lighting changes
            """
            self.camera.framerate = 30.0
            self.camera.shutter_speed = self.shutter_speed
            self.camera.sensor_mode = self.sensor_mode
            self.camera.exposure_mode = self.exposure_mode
            # may not be necessary, night mode seems to do it automatically
            #self.camera.framerate_range = (0.1, self.framerate)
            self.camera.start_preview()

        logger.info('Waiting for camera module warmup...')

        """
            Give the camera firmware a chance to calibrate the sensor, critical
            for low light
        """
        time.sleep(3)

        with self.camera_lock:
            """
                now set the framerate back to the configured value
            """
            self.camera.framerate = self.framerate

        self.camera_thread = threading.Thread(target = self.camera_loop)
        self.camera_thread.daemon = True
        self.camera_thread.start()



    def get_still_image(self):
        """
            This uses base64 for the image data so the gateway doesn't have to do
            anything but pass it to the `img` tag using the well known inline syntax
        """
        _image_stream = io.BytesIO()
        logger.debug("Capturing image <use_video_port:%s>", self.use_video_port)

        with self.camera_lock:
            # image quality higher than 10 tends to make large images with no
            # meaningful quality improvement.
            cap_start = time.time()
            self.camera.capture(_image_stream, format = 'jpeg', quality = 10, thumbnail = None, use_video_port = self.use_video_port)
            cap_end = time.time()
            logger.debug("Capture took %f seconds", (cap_end - cap_start))

        _image_stream.seek(0)
        image = base64.b64encode(_image_stream.getvalue())
        _image_stream.close()
        return image


    def get_resolution(self):
        """
            This formats the resolution as WxH, which the picamera API will actually
            accept when setting the value in set_resolution(), so it works out
            quite well as we can pass resolution back and forth all the way up
            to the Gateway interface as-is without any further parsing or
            formatting
        """
        with self.camera_lock:
            _width, _height = self.camera.resolution
        resolution = "{}x{}".format(_width, _height)
        return resolution


    def set_resolution(self, resolution):
        with self.camera_lock:
            try:
                self.camera.resolution = resolution
                self.resolution = resolution
                return True
            except Exception as e:
                logger.exception("Failed to set resolution")
                return False


    def get_framerate(self):
        with self.camera_lock:
            _fr = float(self.camera.framerate)
        framerate = "{}".format(_fr)
        return framerate


    def set_framerate(self, framerate):
        with self.camera_lock:
            try:
                self.camera.framerate = framerate
                self.framerate = framerate
                return True
            except Exception as e:
                logger.exception("Failed to set framerate")
                return False


    def get_exposure_mode(self):
        with self.camera_lock:
            _ex = self.camera.exposure_mode
        return _ex


    def set_exposure_mode(self, exposure_mode):
        with self.camera_lock:
            try:
                self.camera.exposure_mode = exposure_mode
                self.exposure_mode = exposure_mode

                return True
            except Exception as e:
                logger.exception("Failed to set exposure mode")
                return False


    def camera_loop(self):
        """
            Camera loop

        """
        logger.info('Camera loop running')

        while True:
            try:
                image = self.get_still_image()
                if self.base64_still_image_value is not None and image is not None:
                    self.ioloop.add_callback(self.base64_still_image_value.notify_of_external_update,
                                             image.decode('utf-8'))
            except Exception as e:
                logger.exception('Exception occured while updating image property')

            try:
                resolution = self.get_resolution()
                if self.resolution_value is not None and resolution is not None:
                    self.ioloop.add_callback(self.resolution_value.notify_of_external_update,
                                             resolution)
            except Exception as e:
                logger.exception('Exception occured while updating resolution property')


            try:
                framerate = self.get_framerate()
                if self.framerate_value is not None and framerate is not None:
                    self.ioloop.add_callback(self.framerate_value.notify_of_external_update,
                                             framerate)
            except Exception as e:
                logger.exception('Exception occured while updating framerate property')


            try:
                exposure_mode = self.get_exposure_mode()
                if self.exposure_mode_value is not None and exposure_mode is not None:
                    self.ioloop.add_callback(self.exposure_mode_value.notify_of_external_update,
                                             exposure_mode)
            except Exception as e:
                logger.exception('Exception occured while updating exposure_mode property')

            wait_interval = 1.0 / float(self.framerate)
            logger.debug("Camera sleeping for %.2f (fps: %.2f)", wait_interval, float(self.framerate))

            time.sleep(wait_interval)


    def webthing_setup(self):

        self.thing = Thing(name = self.device_name, type_ = 'camera', description = 'A Web Thing enabled PiCamera')

        self.resolution_property = Property(self.thing,
                                            'resolution',
                                            metadata = {
                                                'type': 'choice',
                                                'unit': '',
                                                'choices': ['320x240',
                                                            '640x480',
                                                            '800x600',
                                                            '1024x768',
                                                            '1296x972',
                                                            '1640x1232',
                                                            '3280x2464'],
                                                'friendlyName': 'Resolution',
                                                'description': 'The current camera resolution',
                                            },
                                            value = self.resolution_value)

        self.thing.add_property(self.resolution_property)


        self.framerate_property = Property(self.thing,
                                           'framerate',
                                           metadata = {
                                               'type': 'choice',
                                               'unit': 'FPS',
                                               'choices': ["0.0", "0.1", "0.5", "1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0", "10.0", "15.0", "20.0", "30.0"],
                                               'friendlyName': 'Framerate',
                                               'description': 'The current camera frame rate',
                                           },
                                           value = self.framerate_value)

        self.thing.add_property(self.framerate_property)


        self.base64_still_image_property = Property(self.thing,
                                                    'stillImage',
                                                    metadata = {
                                                        'type': 'stillImage',
                                                        'unit': 'base64',
                                                        'friendlyName': 'Image',
                                                        'description': 'A still image from the camera',
                                                    },
                                                    value = self.base64_still_image_value)

        self.thing.add_property(self.base64_still_image_property)

        sorted_exposure_modes = sorted(picamera.PiCamera.EXPOSURE_MODES, key = picamera.PiCamera.EXPOSURE_MODES.__getitem__)

        self.exposure_mode_property = Property(self.thing,
                                               'exposureMode',
                                                metadata = {
                                                    'type': 'choice',
                                                    'unit': '',
                                                    'choices': sorted_exposure_modes,
                                                    'friendlyName': 'Exposure',
                                                    'description': 'A still image from the camera',
                                                },
                                                value = self.exposure_mode_value)
        self.thing.add_property(self.exposure_mode_property)

        if self.si7021_enabled:
            logger.info("Temperature/humidity properties enabled")

            self.temperature_property = Property(self.thing,
                                                 'temperature',
                                                 metadata = {
                                                    'type': 'label',
                                                    'unit': '°',
                                                    'friendlyName': 'Temperature',
                                                    'description': 'The current camera temperature',
                                                 },
                                                 value = self.temperature_value)
            self.thing.add_property(self.temperature_property)


            self.humidity_property = Property(self.thing,
                                              'humidity',
                                              metadata = {
                                                  'type': 'label',
                                                  'unit': '%',
                                                  'friendlyName': 'Humidity',
                                                  'description': 'The current camera humidity level',
                                              },
                                              value = self.humidity_value)
            self.thing.add_property(self.humidity_property)

        self.server = WebThingServer([self.thing], port = self.port)


    def sensor_setup(self):
        if self.si7021_enabled:
            self.sensor_thread = threading.Thread(target = self.sensor_loop)
            self.sensor_thread.daemon = True
            self.sensor_thread.start()

    def get_si7021_values(self):
        temperature = None
        humidity = None

        try:
            # Get I2C bus
            bus = smbus.SMBus(1)

            # SI7021 address, 0x40(64)
            #		0xF5(245)	Select Relative Humidity NO HOLD master mode
            bus.write_byte(0x40, 0xF5)

            time.sleep(0.3)

            # SI7021 address, 0x40(64)
            # Read data back, 2 bytes, Humidity MSB first
            data0 = bus.read_byte(0x40)
            data1 = bus.read_byte(0x40)

            # Convert the data
            humidity = ((data0 * 256 + data1) * 125 / 65536.0) - 6

            time.sleep(0.3)

            # SI7021 address, 0x40(64)
            #       0xF3(243)   Select temperature NO HOLD master mode
            bus.write_byte(0x40, 0xF3)

            time.sleep(0.3)

            # SI7021 address, 0x40(64)
            # Read data back, 2 bytes, Temperature MSB first
            data0 = bus.read_byte(0x40)
            data1 = bus.read_byte(0x40)

            # Convert the data
            temperature = ((data0 * 256 + data1) * 175.72 / 65536.0) - 46.85

            # Convert celsius to fahrenheit
            temperature = (temperature * 1.8) + 32

        except Exception as e:
            logger.exception("Failed to get si7021 sensor data")

        return temperature, humidity


    def sensor_loop(self):
        """
            Sensor loop

        """
        logger.info('Sensor loop running')
        while True:
            try:
                temperature = None
                humidity = None

                if self.si7021_enabled:
                    temperature, humidity = self.get_si7021_values()

                if self.temperature_value is not None and temperature is not None:
                    self.ioloop.add_callback(self.temperature_value.notify_of_external_update,
                                             temperature)

                if self.humidity_value is not None and humidity is not None:
                    self.ioloop.add_callback(self.humidity_value.notify_of_external_update,
                                             humidity)

            except Exception as e:
                logger.exception('Exception occured while updating sensor properties')

            time.sleep(self.sensors_update_interval)




if __name__ == '__main__':

    picamera_web_thing = PiCameraWebThing()
    try:
        logger.info('PiCamera Web Thing ready')
        picamera_web_thing.start()
    except KeyboardInterrupt:
        picamera_web_thing.stop()
    finally:
        picamera_web_thing.cleanup()
