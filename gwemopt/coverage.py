
import os, sys
import numpy as np
import healpy as hp

from astropy.time import Time

import glue.segments, glue.segmentsUtils

import gwemopt.utils
import gwemopt.rankedTilesGenerator
import gwemopt.moc

def combine_coverage_structs(coverage_structs):

    coverage_struct_combined = {}
    coverage_struct_combined["data"] = np.empty((0,5))
    coverage_struct_combined["filters"] = np.empty((0,1))
    coverage_struct_combined["ipix"] = []
    coverage_struct_combined["patch"] = []
    coverage_struct_combined["FOV"] = np.empty((0,1))
    for coverage_struct in coverage_structs:
        coverage_struct_combined["data"] = np.append(coverage_struct_combined["data"],coverage_struct["data"],axis=0)
        coverage_struct_combined["filters"] = np.append(coverage_struct_combined["filters"],coverage_struct["filters"])
        coverage_struct_combined["ipix"] = coverage_struct_combined["ipix"] + coverage_struct["ipix"]
        coverage_struct_combined["patch"] = coverage_struct_combined["patch"] + coverage_struct["patch"]
        coverage_struct_combined["FOV"] = np.append(coverage_struct_combined["FOV"],coverage_struct["FOV"])

    return coverage_struct_combined

def read_coverage(params, telescope, filename):

    nside = params["nside"]
    config_struct = params["config"][telescope]

    lines = [line.rstrip('\n') for line in open(filename)]
    lines = lines[1:]
    lines = filter(None,lines)

    coverage_struct = {}
    coverage_struct["data"] = np.empty((0,5))
    coverage_struct["filters"] = []
    coverage_struct["ipix"] = []
    coverage_struct["patch"] = []

    for line in lines:
        lineSplit = line.split(",")
        ra = float(lineSplit[2])
        dec = float(lineSplit[3])
        mjd = float(lineSplit[4])
        filt = lineSplit[6]
        mag = float(lineSplit[7])

        coverage_struct["data"] = np.append(coverage_struct["data"],np.array([[ra,dec,mjd,mag,config_struct["exposuretime"]]]),axis=0)
        coverage_struct["filters"].append(filt)

        if config_struct["FOV_coverage_type"] == "square":
            ipix, radecs, patch = gwemopt.utils.getSquarePixels(ra, dec, config_struct["FOV_coverage"], nside)
        elif config_struct["FOV_coverage_type"] == "circle":
            ipix, radecs, patch = gwemopt.utils.getCirclePixels(ra, dec, config_struct["FOV_coverage"], nside)

        coverage_struct["patch"].append(patch)
        coverage_struct["ipix"].append(ipix)

    coverage_struct["filters"] = np.array(coverage_struct["filters"])
    coverage_struct["FOV"] = config_struct["FOV_coverage"]*np.ones((len(coverage_struct["filters"]),))

    return coverage_struct

def read_coverage_files(params):

    coverage_structs = []
    for telescope, coverageFile in zip(params["telescopes"],params["coverageFiles"]):
        coverage_struct = read_coverage(params,telescope,coverageFile)
        coverage_structs.append(coverage_struct)

    return combine_coverage_structs(coverage_structs)

def tiles_coverage(params, eventinfo, config_struct, tile_struct):

    nside = params["nside"]
    gpstime = eventinfo["gpstime"]
    mjd_inj = Time(gpstime, format='gps', scale='utc').mjd

    coverage_struct = {}
    coverage_struct["data"] = np.empty((0,5))
    coverage_struct["filters"] = []
    coverage_struct["ipix"] = []
    coverage_struct["patch"] = []

    segmentlist = glue.segments.segmentlist()
    n_windows = len(params["Tobs"]) // 2
    start_segments = mjd_inj + params["Tobs"][::2]
    end_segments = mjd_inj + params["Tobs"][1::2]
    for start_segment, end_segment in zip(start_segments,end_segments):
        segmentlist.append(glue.segments.segment(start_segment,end_segment))

    keys = tile_struct.keys()
    while len(keys) > 0:
        key = keys[0]
        tile_struct_hold = tile_struct[key] 
        exposureTime = tile_struct_hold["exposureTime"]

        mjd_exposure_start = segmentlist[0][0]
        mjd_exposure_end = mjd_exposure_start + exposureTime/86400.0
        if mjd_exposure_end > segmentlist[0][1]:
            mjd_exposure_end = segmentlist[0][1]
            exposureTime = (mjd_exposure_end - mjd_exposure_start)*86400.0
            tile_struct[key]["exposureTime"] = tile_struct[key]["exposureTime"] - exposureTime 
        else:
            del tile_struct[key]
            keys.pop(0) 

        segment = glue.segments.segment(mjd_exposure_start,mjd_exposure_end)
        segmentlist = segmentlist - glue.segments.segmentlist([segment])
        segmentlist.coalesce()

        mjd_exposure_mid = (mjd_exposure_start+mjd_exposure_end)/2.0
        nexp = np.round(exposureTime/config_struct["exposuretime"])
        nmag = np.log(nexp) / np.log(2.5)
        mag = config_struct["magnitude"] + nmag

        coverage_struct["data"] = np.append(coverage_struct["data"],np.array([[tile_struct_hold["ra"],tile_struct_hold["dec"],mjd_exposure_mid,mag,exposureTime]]),axis=0)
        coverage_struct["filters"].append(config_struct["filter"])
        coverage_struct["patch"].append(tile_struct_hold["patch"])
        coverage_struct["ipix"].append(tile_struct_hold["ipix"])

    coverage_struct["filters"] = np.array(coverage_struct["filters"])
    coverage_struct["FOV"] = config_struct["FOV"]*np.ones((len(coverage_struct["filters"]),))

    return coverage_struct

def waw(params, eventinfo, map_struct, tile_structs=None, doPlots = False): 

    nside = params["nside"]

    #t = np.arange(0,2,1/96.0)
    t = np.arange(0,7,1.0)
    cr90 = map_struct["cumprob"] < 0.9
    detmaps = gwemopt.waw.detectability_maps(params, t, map_struct, verbose=True, limit_to_region=cr90, nside=nside)

    n_windows = len(params["Tobs"]) // 2
    tot_obs_time = np.sum(np.diff(params["Tobs"])[::2]) * 86400.

    coverage_structs = []
    if tile_structs == None:
        for telescope in params["telescopes"]:
            config_struct = params["config"][telescope]

            Afov = config_struct["FOV"]**2
            T_int = config_struct["exposuretime"]
            strategy_struct = gwemopt.waw.construct_followup_strategy(map_struct["prob"],detmaps,t,Afov,T_int,params["Tobs"],limit_to_region=cr90)

            nside = hp.pixelfunc.get_nside(strategy_struct)
            npix = hp.nside2npix(nside)
            theta, phi = hp.pix2ang(nside, np.arange(npix))
            ra = np.rad2deg(phi)
            dec = np.rad2deg(0.5*np.pi - theta)

            idx = np.where(strategy_struct>0.0)[0]
            strategyfile = os.path.join(params["outputDir"],'strategy.dat')
            fid = open(strategyfile,'w')
            for r,d,s in zip(ra[idx],dec[idx],strategy_struct[idx]):
                fid.write('%.5f %.5f %.5f\n'%(r,d,s))
            fid.close()
    else:
        for telescope in params["telescopes"]: 
            moc_struct = moc_structs[telescope]
            config_struct = params["config"][telescope]
            T_int = config_struct["exposuretime"]
            strategy_struct = gwemopt.waw.construct_followup_strategy_moc(map_struct["prob"],detmaps,t,moc_struct,T_int,params["Tobs"])

        coverage_structs.append(coverage_struct)

    if doPlots:
        gwemopt.plotting.strategy(params,detmaps,t,strategy_struct)

    return combine_coverage_structs(coverage_structs)

def greedy(params, eventinfo, tile_structs):

    coverage_structs = []
    for telescope in params["telescopes"]:
        config_struct = params["config"][telescope]
        tile_struct = tile_structs[telescope]
        coverage_struct = tiles_coverage(params, eventinfo, config_struct, tile_struct)
        coverage_structs.append(coverage_struct)

    return combine_coverage_structs(coverage_structs)