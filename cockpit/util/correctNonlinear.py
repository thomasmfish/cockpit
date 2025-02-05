#!/usr/bin/env python
# -*- coding: utf-8 -*-

## Copyright (C) 2021 University of Oxford
##
## This file is part of Cockpit.
##
## Cockpit is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## Cockpit is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Cockpit.  If not, see <http://www.gnu.org/licenses/>.

## Copyright 2013, The Regents of University of California
##
## Redistribution and use in source and binary forms, with or without
## modification, are permitted provided that the following conditions
## are met:
##
## 1. Redistributions of source code must retain the above copyright
##   notice, this list of conditions and the following disclaimer.
##
## 2. Redistributions in binary form must reproduce the above copyright
##   notice, this list of conditions and the following disclaimer in
##   the documentation and/or other materials provided with the
##   distribution.
##
## 3. Neither the name of the copyright holder nor the names of its
##   contributors may be used to endorse or promote products derived
##   from this software without specific prior written permission.
##
## THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
## "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
## LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
## FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
## COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
## INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
## BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
## LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
## CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
## LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
## ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
## POSSIBILITY OF SUCH DAMAGE.


# Correct data generated by a camera with a nonlinear response, using a 
# map of the camera's response as measured using flatfield data.
# 
# % correctNonlinear.py -map mapFile -suf suf -data file1 file2 ... fileN
# * -map mapFile: Correction file generated by the "Response Map" experiment
#   in the OMX Cockpit program.
# * -suf suf: attach "suf" to corrected files. Defaults to "-linearized".
# * -data file1 ..: files to be corrected.
# 
# The program will use the mapFile file to map the response curve of the 
# camera. Because we can't assume an overall linear response, we have no 
# objective determination of the number of photons incident on the sensor. 
# We must instead rely on the exposure time as a proxy for photons; the 
# exposure time is stored in the extended header of the mapFile. Thus we can
# map exposure time to average counts for each pixel. We must linearly
# interpolate between values for areas in which we do not have data. 
# 
# By making a linear fit for each pixel's response curve, we can force the data
# to linearity (generate a mapping of reported counts to linearized counts). 
# I'm uncertain how closely this response corresponds to the true photon
# count; I wouldn't count on them bearing much resemblance.

from cockpit.util import datadoc

import numpy
import scipy.interpolate
import sys
import time


class Corrector:
    ## \param exposureTimes List of exposure times, one for each image.
    # \param mapData List of 2D numpy arrays mapping out the response curve.
    def __init__(self, exposureTimes, mapData):
        ## Shape of an image.
        self.imageShape = mapData[0].shape

        ## In-order array of exposure times.
        self.exposureTimes = numpy.array(exposureTimes, dtype = numpy.float32)
        ## 3D array of pixel values for each pixel for each exposure time.
        self.imageData = numpy.array(mapData, dtype = numpy.float32)

        # Generate a linear fit for the data as a whole, so we can map any 
        # exposure time to a single value in counts.
        self.slope, self.intercept = numpy.polyfit(self.exposureTimes, 
                map(numpy.mean, self.imageData), 1)
        print ("Linear fit constructed")

        # Break our data up into clusters based on how far apart exposure times
        # are, and ensure that we have data for the very bottom and top of 
        # the data range (assuming 0 and 2**16 are the limits).
        ## List of SubCorrectors covering all valid data ranges.
        self.subCorrectors = []
        exposureSpacing = numpy.median(
                self.exposureTimes[1:] - self.exposureTimes[:-1])
        print ("Calculate a median spacing of",exposureSpacing)
        curIndices = [] # List of indices in the current cluster of data
        for i, expTime in enumerate(self.exposureTimes):
            if i == 0:
                # Can't do anything yet.
                curIndices.append(i)
                continue
            prevTime = self.exposureTimes[i - 1]
            if expTime - prevTime > exposureSpacing * 10.0:
                # Large gap between exposure times; make new SubCorrectors.
                # One nonlinear for the current cluster, one linear for the gap.
                self.subCorrectors.append(
                        SubCorrector(self.exposureTimes[curIndices],
                            self.imageData[curIndices], 2))
                curIndices = [i - 1, i]
                self.subCorrectors.append(
                        SubCorrector(self.exposureTimes[curIndices],
                            self.imageData[curIndices], 2))
                curIndices = []
            curIndices.append(i)

        # Clean up the remainder.
        self.subCorrectors.append(SubCorrector(self.exposureTimes[curIndices], 
                self.imageData[curIndices], 2))
        # Ensure we have linear mapping to the bottom and top of the dataset, 
        # by linearly extrapolating out to -maxint and maxint. We fit lines to
        # our bottom and top subcorrectors and extrapolate along their slopes.
        newCorrectors = []
        for startIndex, target in [(0, -(2 ** 16) + 1), (-1, (2 ** 16) - 1)]:
            corrector = self.subCorrectors[startIndex]
            xVals = corrector.exposureTimes
            yVals = corrector.imageData
            print ("Extrapolating with",xVals,[numpy.median(v) for v in yVals])
            yVals.shape = len(xVals), numpy.product(self.imageShape)
            slopes, intercepts = numpy.polyfit(xVals, yVals, 1)
            # Restore our previous shape.
            yVals.shape = len(xVals), self.imageShape[0], self.imageShape[1]
            # Calculate extrapolated "exposure times" at the target.
            extrapolated = slopes * target + intercepts
            extrapolated.shape = self.imageShape
            
            images = [corrector.imageData[startIndex]]
            times = [corrector.exposureTimes[startIndex]]
            if startIndex == 0:
                images.insert(0, extrapolated)
                times.insert(0, -self.intercept / self.slope)
                print ("Extrapolated to negative time at",times[0],numpy.median(images[0]))
            else:
                images.append(extrapolated)
                times.append(target - self.intercept / self.slope)
            images = numpy.array(images)
            newCorrectors.append(SubCorrector(times, images, 1))
        self.subCorrectors.extend(newCorrectors)


    ## Given an input data array (2D), return a corrected version.
    # See this post for a non-vectorized version of this algorithm:
    # http://mail.scipy.org/pipermail/scipy-user/2013-January/034051.html
    # Each SubCorrector can only handle a certain range of inputs, so we must
    # apply them iteratively to cover all the input data.
    def correct(self, inputData):
        # Start out with "all uncorrected" data.
        result = numpy.ones(inputData.shape, dtype = numpy.float32) * -1
        for corrector in self.subCorrectors:
            exposures = corrector.correct(inputData)
            validIndices = numpy.where(exposures != -1)
            result[validIndices] = exposures[validIndices]
            if numpy.all(result != -1):
                # All done
                break
        if numpy.any(result == -1):
            # We failed to correct some of the pixels. This should only be an
            # issue if our response map is basically flat at one end (the 
            # low end, most likely), such that our extrapolation of the 
            # curve doesn't cover the camera's full range. For example, if our
            # map of a pixel flattens at the bottom at 108 counts, then our
            # extrapolation past the end of the map data will not go below 108,
            # and if the pixel actually reads 107 then we can't correct it.
            badPixels = numpy.where(result == -1)
            print ("Failed to correct %d pixels" % len(badPixels[0]))
            for x, y in zip(*badPixels):
                print ("(%d, %d): %d, %s" % (x, y, inputData[x, y], ', '.join([c.describe(x, y) for c in self.subCorrectors])))
            # Is this really the best thing to do here? It seems better than
            # just leaving -1 in the result, which will get wrapped to 65535
            # when we save it...
            result[badPixels] = inputData[badPixels]
        # Convert from "exposure time" to "counts", purely to give the user
        # numbers in the range they're used to dealing with from the camera.
        return result * self.slope + self.intercept



## This class is a subcontractor to the Corrector class, responsible for 
# linearizing a portion of the data range. We do this because it's expected
# that the response curve map is dense where the curve is nonlinear and sparse
# otherwise. Our correcting system requires us to have a synthetic uniform
# sampling of the response curve, which gets prohibitive in memory usage if 
# we try to do it for the entire possible data range, so we split it up into
# sections.
class SubCorrector:
    ## \param exposureTimes List of exposure times we are valid for.
    # \param images List of 2D image arrays of corresponding exposure times.
    # \param sampleRate Amount of supersampling we should perform when we 
    #        create a uniform sampling of the image data.
    def __init__(self, exposureTimes, images, sampleRate = 1):
        print ("Making subcontractor with times/median values","\n".join([str((t, numpy.median(d))) for t, d in zip(exposureTimes, images)]))
        self.exposureTimes = exposureTimes
        self.imageData = images
        self.imageShape = images.shape[1:]

        # We now need to uniformly sample our input data so that we can 
        # efficiently invert the exposure time -> counts function. See
        # thread starting here:
        # http://mail.scipy.org/pipermail/scipy-user/2013-January/034032.html
        # (continues on into February)
        self.minVals = self.imageData.min(axis = 0)
        self.maxVals = self.imageData.max(axis = 0)

        self.numSamples = len(self.imageData) * sampleRate
        sampledShape = (self.numSamples, self.imageShape[0], self.imageShape[1])
        self.uniformData = numpy.zeros(sampledShape, dtype = numpy.float32)
        self.uniformExposures = numpy.zeros(sampledShape, dtype = numpy.float32)
        for i in range(self.imageShape[0]):
            for j in range(self.imageShape[1]):
                self.uniformData[:, i, j] = numpy.linspace(self.minVals[i, j], 
                        self.maxVals[i, j], self.numSamples)
                self.uniformExposures[:, i, j] = numpy.interp(
                        self.uniformData[:, i, j], self.imageData[:, i, j], 
                        self.exposureTimes)


    ## Given an input 2D array, linearize it so that its values are in terms
    # of "exposure time" instead of counts. If a value is outside the range 
    # we can handle, then return -1 for that pixel.
    def correct(self, inputData):
        indices = (self.numSamples - 1) * (inputData - self.minVals) / (self.maxVals - self.minVals)
        mapInput = numpy.zeros((3, self.imageShape[0], self.imageShape[1]))
        mapInput[:1] = indices
        mapInput[1:] = numpy.indices(self.imageShape)
        exposures = scipy.ndimage.interpolation.map_coordinates(
                self.uniformExposures, mapInput, order = 1, cval = -1)
        return exposures


    ## Return a description of our mapping for the specified pixel.
    def describe(self, x, y):
        return "(%.2f @ %.2f, %.2f @ %.2f)" % (self.uniformData[0, x, y], self.exposureTimes[0], self.uniformData[-1, x, y], self.exposureTimes[-1])


    def __repr__(self):
        return "<SubCorrector with range (%.2f %d, %.2f %d)>" % (self.exposureTimes[0], numpy.median(self.uniformData[0]), self.exposureTimes[-1], numpy.median(self.uniformData[-1]))




if __name__ == '__main__':
    mapFile = None
    dataFiles = []
    suffix = '-linearized'
    curItem = None
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '-map':
            i += 1
            mapFile = sys.argv[i]
        elif arg == '-data':
            curItem = dataFiles
        elif arg == '-suf':
            i += 1
            suffix = sys.argv[i]
        else:
            curItem.append(arg)
        i += 1

    # Load the map file and separate out the exposure times from the individual
    # averaged images.
    expTimeToData = {}
    mapFile = datadoc.DataDoc(mapFile)
    for z in range(mapFile.size[2]):
        exposureTime = mapFile.extendedHeaderFloats[0, 0, z, 0, 0]
        data = mapFile.imageArray[0, 0, z]
        expTimeToData[exposureTime] = data

    expDataPairs = sorted(expTimeToData.items())
    exposureTimes = [e[0] for e in expDataPairs]
    mapData = [e[1] for e in expDataPairs]

    print ("Loaded exposure time / mean value pairs:")
    print ("\n".join(map(str, [(t, numpy.mean(d)) for t, d in expDataPairs])))

    start = time.time()
    corrector = Corrector(exposureTimes, mapData)
    timeToMake = time.time() - start
    correctionTimes = []
    for filename in dataFiles:
        inputData = datadoc.DataDoc(filename).imageArray
    #    print ("Loading",filename,"initial stats",inputData.min(),inputData.max(),numpy.median(inputData),numpy.std(inputData))
        result = numpy.zeros(inputData.shape, dtype = numpy.float32)
        subStart = time.time()
        for wavelength in range(inputData.shape[0]):
            for timepoint in range(inputData.shape[1]):
                for z in range(inputData.shape[2]):
                    print (filename, timepoint, z)
                    result[wavelength, timepoint, z] = corrector.correct(inputData[wavelength, timepoint, z])
        correctionTimes.append(time.time() - subStart)
        datadoc.writeDataAsMrc(result, filename + suffix)
    #    print ("%s: %.2f, %.2f, %.2f, %.2f" % (filename, result.min(), result.max(), numpy.mean(result), numpy.std(result)))

    overallTime = time.time() - start
    print ("Initialization took %.2f; correction took on average %.2f; overall %.2f for %d files" % (timeToMake, numpy.mean(correctionTimes), overallTime, len(dataFiles)))
