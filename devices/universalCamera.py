#!/usr/bin/python
# -*- coding: utf-8
#
# Copyright 2016 Mick Phillips (mick.phillips@gmail.com)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""UniversalCamera device.

Supports cameras which implement the interface defined in 
  microscope.camera.Camera ."""

import decimal
import Pyro4
import wx

import camera
import events
import gui.device
import gui.guiUtils
import gui.toggleButton
import handlers.camera
import util.listener
import util.threads
from gui.device import SettingsEditor

CLASS_NAME = 'UniversalCameraManager'

# The following must be defined as in handlers/camera.py
(TRIGGER_AFTER, TRIGGER_BEFORE, TRIGGER_DURATION, TRIGGER_SOFT) = range(4)


class UniversalCameraDevice(camera.CameraDevice):
    """A class to control Andor cameras via the pyAndor remote interface."""
    def __init__(self, cam_config):
        # camConfig is a dict with containing configuration parameters.
        super(UniversalCameraDevice, self).__init__(cam_config)
        self.handler = None        
        self.enabled = False
        self.panel = None
        self.config = cam_config
        # Pyro proxy
        self.proxy = Pyro4.Proxy('PYRO:%s@%s:%d' %
                                 ('DeviceServer',
                                   cam_config.get('ipAddress') or cam_config.get('host'),
                                   cam_config.get('port')))
        self.listener = util.listener.Listener(self.proxy,
                                               lambda *args: self.receiveData(*args))
        self.base_transform = cam_config.get('baseTransform') or (0, 0, 0)
        self.path_transform = (0, 0, 0)
        self.settings = {}
        self.cached_settings={}
        self.settings['transform'] = tuple(
                         self.path_transform[i] ^ self.base_transform[i] for i in range(3))
        self.settings['exposure_time'] = 0.001
        self.settings_editor = None


    def cleanupAfterExperiment(self):
        """Restore settings as they were prior to experiment."""
        if self.enabled:
            self.settings.update(self.cached_settings)
            self.proxy.update_settings(self.settings)
            self.proxy.enable()
        self.handler.exposureMode = self.proxy.get_trigger_type()


    def performSubscriptions(self):
        """Perform subscriptions for this camera."""
        events.subscribe('cleanup after experiment',
                self.cleanupAfterExperiment)
        events.subscribe('objective change',
                self.onObjectiveChange)


    def onObjectiveChange(self, name, pixelSize, transform, offset):
        self.settings.update({'pathTransform': transform})
        # Apply the change now if the camera is enabled.
        if self.enabled:
            self.proxy.update_settings(self.settings)
    

    def getHandlers(self):
        """Return camera handlers."""
        result = handlers.camera.CameraHandler(
                "%s" % self.config.get('label'), "iXon camera",
                {'setEnabled': self.enableCamera,
                 'getImageSize': self.getImageSize,
                 'getTimeBetweenExposures': self.getTimeBetweenExposures,
                 'prepareForExperiment': self.prepareForExperiment,
                 'getExposureTime': self.getExposureTime,
                 'setExposureTime': self.setExposureTime,
                 'getImageSizes': self.getImageSizes,
                 'setImageSize': self.setImageSize,
                 'getSavefileInfo': self.getSavefileInfo,
                 'makeUI': self.makeUI,
                 'softTrigger': self.softTrigger},
                TRIGGER_SOFT) # will be set with value from hardware later
        self.handler = result
        self.handler.addListener(self)
        return result

    def enableCamera(self, name, shouldEnable):
        """Enable the hardware."""
        if not shouldEnable:
            # Disable the camera, if it is enabled.
            if self.enabled:
                self.enabled = False
                self.proxy.disable()
                self.proxy.make_safe()
                self.listener.disconnect()
                return self.enabled

        # Enable the camera
        if self.enabled:
            # Nothing to do.
            return
        # Use async call to allow hardware time to respond.
        result = Pyro4.async(self.proxy).enable()
        result.wait(timeout=10)
        #raise Exception("Problem enabling %s." % self.name)
        self.enabled = True
        return self.enabled


    def onEnabledEvent(self, evt=None):
        if self.enabled:
            self.settings.update(self.proxy.get_all_settings())
            self.handler.exposureMode = self.proxy.get_trigger_type()
            self.listener.connect()


    def onPyroError(self, err, *args):
        """Handle exceptions raised by aync. proxy."""
        raise err


    def getExposureTime(self, name=None, isExact=False):
        """Read the real exposure time from the camera."""
        # Camera uses times in s; cockpit uses ms.
        t = self.proxy.get_exposure_time()
        if isExact:
            return decimal.Decimal(t) * (decimal.Decimal(1000.0))
        else:
            return t * 1000.0


    def getImageSize(self, name):
        """Read the image size from the camera."""
        rect = self.proxy.get_roi()
        return rect[-2:]


    def getImageSizes(self, name):
        """Return a list of available image sizes."""
        return []


    def getSavefileInfo(self, name):
        """Return an info string describing the measurement."""
        #return "%s: %s image" % (name, self.imageSize)
        return ""


    def getTimeBetweenExposures(self, name, isExact=False):
        """Get the amount of time between exposures.

        This is the time that must pass after stopping one exposure
        before another can be started, in milliseconds."""
        # Camera uses time in s; cockpit uses ms.
        t = self.proxy.get_cycle_time() * 1000.0
        if isExact:
            result = decimal.Decimal(t)
        else:
            result = t
        return result


    def prepareForExperiment(self, name, experiment):
        """Make the hardware ready for an experiment."""
        self.cached_settings.update(self.settings)


    def receiveData(self, *args):
        """This function is called when data is received from the hardware."""
        (image, timestamp) = args
        events.publish('new image %s' % self.name, image, timestamp)


    def setExposureTime(self, name, exposureTime):
        """Set the exposure time."""
        # Camera uses times in s; cockpit uses ms.
        self.proxy.set_exposure_time(exposureTime / 1000.0)


    def setImageSize(self, name, imageSize):
        pass



    def softTrigger(self, name=None):
        self.proxy.soft_trigger()



   ### UI functions ###
    def makeUI(self, parent):
        self.panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        adv_button = gui.device.Button(parent=self.panel,
                                       label='settings',
                                       leftAction=self.showSettings)
        sizer.Add(adv_button)
        self.panel.SetSizerAndFit(sizer)
        return self.panel


    def showSettings(self, evt):
        if not self.settings_editor:
            # TODO - there's a problem with abstraction here. The settings
            # editor needs the describe/get/set settings functions from the
            # proxy, but it also needs to be able to invalidate the cache
            # on the handler. The handler should probably expose the
            # settings interface. UniversalCamera is starting to look
            # more and more like an interface translation.
            self.settings_editor = SettingsEditor(self.proxy, handler=self.handler)
            self.settings_editor.Show()
        self.settings_editor.SetPosition(wx.GetMousePosition())
        self.settings_editor.Raise()


class UniversalCameraManager(camera.CameraManager):
    _CAMERA_CLASS = UniversalCameraDevice
    _SUPPORTED_CAMERAS = 'universalCamera'
