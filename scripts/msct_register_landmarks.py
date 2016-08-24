#!/usr/bin/env python
#########################################################################################
#
# This file contains an implementation of the iterative closest point algorithm.
# This algo registers two sets of points (3D coordinates) together.
#
# Adapted from: http://stackoverflow.com/questions/20120384/iterative-closest-point-icp-implementation-on-python
#
# ---------------------------------------------------------------------------------------
# Copyright (c) 2013 Polytechnique Montreal <www.neuro.polymtl.ca>
# Author: Benjamin De Leener
# Created: 2015-06-10
#
# About the license: see the file LICENSE.TXT
#########################################################################################

# TODO: add full affine transfo
# TODO: normalize SSE: currently, it depends on the number of landmarks

from msct_types import Point
from numpy import array, sin, cos, matrix, sum, mean, absolute
from math import pow, sqrt
from operator import itemgetter
from msct_register_regularized import generate_warping_field
import sct_utils as sct
from nibabel import load

sse_results = []
ini_param_rotation = 0.5 #rad
ini_param_trans_x = 270.0 #pix
ini_param_trans_y = -150.0 #pix
initial_step = 2

def register_landmarks(fname_src,
                       fname_dest,
                       dof,
                       fname_affine='affine.txt'):
    """
    Register two NIFTI volumes containing landmarks
    :param fname_src: fname of source landmarks
    :param fname_dest: fname of destination landmarks
    :param dof: degree of freedom. Separate with "_". Example: Tx_Ty_Tz_Rx_Ry_Sz
    :param fname_affine: output affine transformation
    :return:
    """
    from msct_image import Image
    # open src label
    im_src = Image(fname_src)
    coord_src = im_src.getNonZeroCoordinates(sorting='value')  # landmarks are sorted by value
    # open dest labels
    im_dest = Image(fname_dest)
    coord_dest = im_dest.getNonZeroCoordinates(sorting='value')
    # Reorganize landmarks
    points_fixed, points_moving = [], []
    for coord in coord_src:
        point_straight = im_src.transfo_pix2phys([[coord.x, coord.y, coord.z]])
        points_moving.append([point_straight[0][0], point_straight[0][1], point_straight[0][2]])
    for coord in coord_dest:
        point_template = im_dest.transfo_pix2phys([[coord.x, coord.y, coord.z]])
        points_fixed.append([point_template[0][0], point_template[0][1], point_template[0][2]])
    # display
    sct.printv('Labels src: ' + str(points_moving), 1)
    sct.printv('Labels dest: ' + str(points_fixed), 1)
    # check if landmarks match pairwise
    # TODO
    # get DOF
    # dof = 'Tx_Ty_Tz_Rx_Ry_Sz' #'translation-scaling-z'
    (rotation_matrix, translation_array, points_moving_reg, points_moving_barycenter) = getRigidTransformFromLandmarks(points_moving, points_fixed, constraints=dof, show=1)
    # writing rigid transformation file
    # N.B. for some reason, the moving and fixed points are inverted between ITK transform and our python-based transform.
    # and for another unknown reason, x and y dimensions have a negative sign (at least for translation and center of rotation).
    text_file = open(fname_affine, 'w')
    text_file.write("#Insight Transform File V1.0\n")
    text_file.write("#Transform 0\n")
    text_file.write("Transform: AffineTransform_double_3_3\n")
    text_file.write("Parameters: %.9f %.9f %.9f %.9f %.9f %.9f %.9f %.9f %.9f %.9f %.9f %.9f\n" % (
        rotation_matrix[0, 0], rotation_matrix[0, 1], rotation_matrix[0, 2],
        rotation_matrix[1, 0], rotation_matrix[1, 1], rotation_matrix[1, 2],
        rotation_matrix[2, 0], rotation_matrix[2, 1], rotation_matrix[2, 2],
        -translation_array[0, 0], -translation_array[0, 1], translation_array[0, 2]))
    text_file.write("FixedParameters: %.9f %.9f %.9f\n" % (-points_moving_barycenter[0],
                                                           -points_moving_barycenter[1],
                                                           points_moving_barycenter[2]))
    text_file.close()



def getNeighbors(point, set_points, k=1):
    '''
    Locate most similar neighbours
    :param point: the point for which we want to compute the neighbors
    :param trainingSet: list of other Points
    :param k: number of neighbors wanted
    :return: k nearest neighbors of input point
    '''
    distances = []
    for other_point in set_points:
        dist = point.euclideanDistance(other_point)
        distances.append((other_point, dist))
    distances.sort(key=itemgetter(1))
    return [distances[x][0] for x in range(k)]


def SSE(pointsA, pointsB):
    return sum(array(pointsA[:, 0:3]-pointsB[:, 0:3])**2.0)

def real_optimization_parameters(param_from_optimizer, initial_param = 0, initial_step = 10):
    # The initial step for the Nelder-Mead algorithm is of (initial_param * 5e-2) which is too small when we want initial_param = 30 pix and step = 5 or 10.
    # This function allows to choose the scale of the steps after the first movement
    step_factor = float(initial_step)/float(initial_param*5e-2)
    real_param = initial_param + (param_from_optimizer - initial_param) * step_factor

    return real_param

def Metric_Images(imageA, imageB, type=''):

    data_A_list = load(imageA).get_data().tolist()
    data_B_list = load(imageB).get_data().tolist()

    # Define both list of intensity
    list_A = []
    list_B = []
    for i in range(len(data_A_list)):
        list_A = list_A + data_A_list[i]
        list_B = list_B + data_B_list[i]
    # Calculate metric depending on the type
    if type == 'MeanSquares':
        result_metric = 1.0/(len(list_A)) * sum(array([list_A[i][0] - list_B[i][0] for i in range(len(list_A))])**2)
        #result_metric = 1/(len(list_A)) * sum(array(list_A - list_B)**2)

    if type == 'Correlation':
        result_metric = 1.0/(len(list_A)) * sum(absolute(array([list_A[i][0] - list_B[i][0] for i in range(len(list_A))])))

    if type == 'MI':
        print '\nto do: MI'

    # Return results
    print '\nResult of metric is: '+str(result_metric)
    return result_metric


def minimize_transform(params, points_fixed, points_moving, constraints):
    # initialize dof
    dof = [0, 0, 0, 0, 0, 0, 1, 1, 1]
    # initialize dictionary to relate constraints index to dof
    dict_dof = {'Tx': 0, 'Ty': 1, 'Tz': 2, 'Rx': 3, 'Ry': 4, 'Rz': 5, 'Sx': 6, 'Sy': 7, 'Sz': 8}
    # extract constraints
    list_constraints = constraints.split('_')
    # loop across constraints and update dof
    for i in range(len(list_constraints)):
        dof[dict_dof[list_constraints[i]]] = params[i]
    # convert dof to more intuitive variables
    tx, ty, tz, alpha, beta, gamma, scx, scy, scz = dof[0], dof[1], dof[2], dof[3], dof[4], dof[5], dof[6], dof[7], dof[8]
    # build rotation matrix
    rotation_matrix = matrix([[cos(alpha)*cos(beta), cos(alpha)*sin(beta)*sin(gamma)-sin(alpha)*cos(gamma), cos(alpha)*sin(beta)*cos(gamma)+sin(alpha)*sin(gamma)],
                              [sin(alpha)*cos(beta), sin(alpha)*sin(beta)*sin(gamma)+cos(alpha)*cos(gamma), sin(alpha)*sin(beta)*cos(gamma)-cos(alpha)*sin(gamma)],
                              [-sin(beta), cos(beta)*sin(gamma), cos(beta)*cos(gamma)]])
    # build scaling matrix
    scaling_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
    # compute rotation+scaling matrix
    rotsc_matrix = scaling_matrix * rotation_matrix
    # compute center of mass from moving points (src)
    points_moving_barycenter = mean(points_moving, axis=0)
    # apply transformation to moving points (src)
    points_moving_reg = ((rotsc_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + matrix([tx, ty, tz])
    # record SSE for later display
    sse_results.append(SSE(matrix(points_fixed), points_moving_reg))
    # return SSE
    return SSE(matrix(points_fixed), points_moving_reg)
#
# def minAffineTransform(params, points_fixed, points_moving):
#     alpha, beta, gamma, tx, ty, tz, scx, scy, scz = params[0], params[1], params[2], params[3], params[4], params[5], params[6], params[7], params[8]
#
#     scaling_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
#     rotation_matrix = matrix([[cos(alpha)*cos(beta), cos(alpha)*sin(beta)*sin(gamma)-sin(alpha)*cos(gamma), cos(alpha)*sin(beta)*cos(gamma)+sin(alpha)*sin(gamma)],
#                               [sin(alpha)*cos(beta), sin(alpha)*sin(beta)*sin(gamma)+cos(alpha)*cos(gamma), sin(alpha)*sin(beta)*cos(gamma)-cos(alpha)*sin(gamma)],
#                               [-sin(beta), cos(beta)*sin(gamma), cos(beta)*cos(gamma)]])
#     rotsc_matrix = scaling_matrix * rotation_matrix
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#
#     points_moving_reg = ((rotsc_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + matrix([tx, ty, tz])
#     sse_results.append(SSE(matrix(points_fixed), points_moving_reg))
#
#     return SSE(matrix(points_fixed), points_moving_reg)
#
#
# def minRigidTransform(params, points_fixed, points_moving):
#     alpha, beta, gamma, tx, ty, tz = params[0], params[1], params[2], params[3], params[4], params[5]
#
#     rotation_matrix = matrix([[cos(alpha)*cos(beta), cos(alpha)*sin(beta)*sin(gamma)-sin(alpha)*cos(gamma), cos(alpha)*sin(beta)*cos(gamma)+sin(alpha)*sin(gamma)],
#                               [sin(alpha)*cos(beta), sin(alpha)*sin(beta)*sin(gamma)+cos(alpha)*cos(gamma), sin(alpha)*sin(beta)*cos(gamma)-cos(alpha)*sin(gamma)],
#                               [-sin(beta), cos(beta)*sin(gamma), cos(beta)*cos(gamma)]])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#
#     points_moving_reg = ((rotation_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + matrix([tx, ty, tz])
#     sse_results.append(SSE(matrix(points_fixed), points_moving_reg))
#
#     return SSE(matrix(points_fixed), points_moving_reg)
#
# def minTranslationScalingTransform(params, points_fixed, points_moving):
#     scx, scy, scz, tx, ty, tz = params[0], params[1], params[2], params[3], params[4], params[5]
#
#     rotation_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
#     translation_array = matrix([tx, ty, tz])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#     points_moving_reg = ((rotation_matrix * (
#         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array
#
#     return SSE(matrix(points_fixed), points_moving_reg)
#
# def minTranslationScalingZTransform(params, points_fixed, points_moving):
#     scz, tx, ty, tz = params[0], params[1], params[2], params[3]
#
#     rotation_matrix = matrix([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, scz]])
#     translation_array = matrix([tx, ty, tz])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#     points_moving_reg = ((rotation_matrix * (
#         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array
#
#     sse_results.append(SSE(matrix(points_fixed), points_moving_reg))
#     return SSE(matrix(points_fixed), points_moving_reg)
#
# def minRigid_xy_Transform(params, points_fixed, points_moving):
#     gamma, tx, ty = params[0], params[1], params[2]
#
#     rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
#                               [sin(gamma), cos(gamma), 0],
#                               [0, 0, 1]])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#
#     points_moving_reg = ((rotation_matrix * (
#         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + matrix([tx, ty, 0])
#     sse_results.append(SSE(matrix(points_fixed), points_moving_reg))
#     return SSE(matrix(points_fixed), points_moving_reg)
#
# def minRigid_xy_Transform_for_Images(params, image_fixed, image_moving, center_rotation = None, metric='MeanSquares'):
#     gamma, tx, ty = params[0], params[1], params[2]
#     name_warp = 'warping_field.nii.gz'
#     path, file, ext = sct.extract_fname(image_moving)
#     name_image_moving_reg = file + '_reg' + ext
#
#     # Apply transformation
#     tx_real = real_optimization_parameters(tx, initial_param=ini_param_trans_x, initial_step=initial_step)
#     ty_real = real_optimization_parameters(ty, initial_param=ini_param_trans_y, initial_step=initial_step)
#     generate_warping_field(image_fixed, [tx_real], [ty_real], [gamma], center_rotation=center_rotation, fname=name_warp)
#     print'\nApplying a rigid transformation of parameters: angle=' + str(gamma) + 'rad, ' + 'tx=' + str(tx_real) +'pix, ty=' + str(ty_real) + 'pix'
#     sct.run('sct_apply_transfo -i ' + image_moving + ' -d ' + image_fixed + ' -w ' + name_warp + ' -o ' + name_image_moving_reg + ' -x nn')
#
#     # return metric results of the transformation image compared to the fixed image
#     return Metric_Images(image_fixed, name_image_moving_reg, type=metric)
#
# def minTranslation_Transform(params, points_fixed, points_moving):
#     return SSE(matrix(points_fixed), matrix(points_moving) + matrix([params[0], params[1], params[2]]))
#
#
# def minTranslation_xy_Transform(params, points_fixed, points_moving):
#     return SSE(matrix(points_fixed), matrix(points_moving) + matrix([params[0], params[1], 0.0]))
#
#
# def minRotation_Transform(params, points_fixed, points_moving):
#     alpha, beta, gamma = params[0], params[1], params[2]
#
#     rotation_matrix = matrix([[cos(alpha)*cos(beta), cos(alpha)*sin(beta)*sin(gamma)-sin(alpha)*cos(gamma), cos(alpha)*sin(beta)*cos(gamma)+sin(alpha)*sin(gamma)],
#                               [sin(alpha)*cos(beta), sin(alpha)*sin(beta)*sin(gamma)+cos(alpha)*cos(gamma), sin(alpha)*sin(beta)*cos(gamma)-cos(alpha)*sin(gamma)],
#                               [-sin(beta), cos(beta)*sin(gamma), cos(beta)*cos(gamma)]])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#
#     points_moving_reg = (rotation_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter
#
#     return SSE(matrix(points_fixed), points_moving_reg)
#
#
# def minRotation_xy_Transform(params, points_fixed, points_moving):
#     gamma = params[0]
#
#     rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
#                               [sin(gamma), cos(gamma), 0],
#                               [0, 0, 1]])
#
#     points_moving_barycenter = mean(points_moving, axis=0)
#
#     points_moving_reg = (rotation_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter
#
#     return SSE(matrix(points_fixed), points_moving_reg)
#
# def minRotation_xy_Transform_for_Images(params, image_fixed, image_moving, center_rotation = None, metric='MeanSquares'):
#     gamma = params[0]
#     name_warp = 'warping_field.nii.gz'
#     path, file, ext = sct.extract_fname(image_moving)
#     name_image_moving_reg = file + '_reg' + ext
#
#     # Apply transformation
#     gamma_list = [gamma]
#     generate_warping_field(image_fixed, [0], [0], gamma_list, center_rotation=center_rotation, fname=name_warp)
#     print'\nApplying a pure rotational warping field of angle ' + str(gamma) + 'rad'
#     sct.run('sct_apply_transfo -i ' + image_moving + ' -d ' + image_fixed + ' -w ' + name_warp + ' -o ' + name_image_moving_reg + ' -x nn')
#
#     # return metric results of the transformation image compared to the fixed image
#     return Metric_Images(image_fixed, name_image_moving_reg, type=metric)
#

def getRigidTransformFromImages(image_fixed, image_moving, constraints='none', metric = 'MeanSquares', center_rotation=None):
    list_constraints = [None, 'none', 'xy', 'translation', 'translation-xy', 'rotation', 'rotation-xy']
    list_center_rotation = [None, 'BarycenterImage']
    if constraints not in list_constraints:
        raise 'ERROR: the constraints must be one of those: '+', '.join(list_constraints)
    if center_rotation not in list_center_rotation:
        raise 'ERROR: the center_rotation must be one of those: '+', '.join(list_center_rotation)

    from scipy.optimize import minimize

    rotation_matrix = matrix([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    translation_array = matrix([0.0, 0.0, 0.0])

    # Get barycenter of the images if specified
    if center_rotation == 'BarycenterImage':
        print '\nEvaluating barycenters of images...'
        # Importing data
        from nibabel import load
        from numpy import amax, cross, dot
        from math import acos, pi
        data_moving = load(image_moving).get_data()
        data_fixed = load(image_fixed).get_data()
        data_moving_10percent = data_moving > amax(data_moving) * 0.1
        data_fixed_10percent = data_fixed > amax(data_fixed) * 0.1
        # Calculating position of barycenters
        coord_barycenter_moving = (1.0/(sum(data_moving))) * sum(array([[data_moving[i,j,k] * i, data_moving[i,j,k] * j, data_moving[i,j,k] * k] for i in range(data_moving.shape[0]) for j in range(data_moving.shape[1]) for k in range(data_moving.shape[2])]), axis=0)
        coord_barycenter_fixed = (1.0/(sum(data_fixed))) * sum(array([[data_fixed[i,j,k] * i, data_fixed[i,j,k] * j, data_fixed[i,j,k] * k] for i in range(data_fixed.shape[0]) for j in range(data_fixed.shape[1]) for k in range(data_fixed.shape[2])]), axis=0)
        coord_barycenter_moving_10percent = (1.0/(sum(data_moving_10percent))) * sum(array([[data_moving_10percent[i,j,k] * i, data_moving_10percent[i,j,k] * j, data_moving_10percent[i,j,k] * k] for i in range(data_moving_10percent.shape[0]) for j in range(data_moving_10percent.shape[1]) for k in range(data_moving_10percent.shape[2])]), axis=0)
        coord_barycenter_fixed_10percent = (1.0/(sum(data_fixed_10percent))) * sum(array([[data_fixed_10percent[i,j,k] * i, data_fixed_10percent[i,j,k] * j, data_fixed_10percent[i,j,k] * k] for i in range(data_fixed_10percent.shape[0]) for j in range(data_fixed_10percent.shape[1]) for k in range(data_fixed_10percent.shape[2])]), axis=0)

        print '\nPosition of the barycenters:' \
              '\n\t-moving image : '+ str(coord_barycenter_moving) +  \
              '\n\t-fixed image: ' + str(coord_barycenter_fixed)
        #Evaluating initial translations to match the barycenters
        ini_param_trans_x_real = int(round(coord_barycenter_fixed[0] - coord_barycenter_moving[0]))
        ini_param_trans_y_real = int(round(coord_barycenter_fixed[1] - coord_barycenter_moving[1]))

        # Defining new center of rotation
        coord_center_rotation = [int(round(coord_barycenter_fixed[0])), int(round(coord_barycenter_fixed[1])), int(round(coord_barycenter_fixed[2]))]

        #Evaluating the initial rotation to match the 10 percent barycenters
        # We have calculated two relevant points to evaluate the best initial registration for the algorithm so that it may converge more quickly
        # First a translation to match the barycenters and then a rotation (of center: the barycenter of the fixed image) to match the 10_percent barycenters
        vector_bar_fix_2_bar_10p_moving = coord_barycenter_moving_10percent - coord_barycenter_fixed
        vector_bar_fix_2_bar_10p_fixed = coord_barycenter_fixed_10percent - coord_barycenter_fixed
        vector_bar_10p_fix_2_10p_moving = coord_barycenter_moving_10percent - coord_barycenter_fixed_10percent
        a = dot(vector_bar_fix_2_bar_10p_moving, vector_bar_fix_2_bar_10p_moving) #OAm
        b = dot(vector_bar_fix_2_bar_10p_fixed, vector_bar_fix_2_bar_10p_fixed) #OAf
        c = dot(vector_bar_10p_fix_2_10p_moving, vector_bar_10p_fix_2_10p_moving) #AfAm
        e = cross(vector_bar_fix_2_bar_10p_moving, vector_bar_fix_2_bar_10p_fixed)
        if e[2] >= 0:
            ini_param_rotation_real = acos((a + b - c)/(2.0*sqrt(a)*sqrt(b)))   # theorem of Al-Kashi
        else:
            ini_param_rotation_real = -acos((a + b - c)/(2.0*sqrt(a)*sqrt(b)))    # theorem of Al-Kashi

    else:
        coord_center_rotation=None
        ini_param_trans_x_real = ini_param_trans_x
        ini_param_trans_y_real = ini_param_trans_y
        ini_param_rotation_real = ini_param_rotation

    if constraints == 'rotation-xy':
        initial_parameters = [ini_param_rotation]
        res = minimize(minRotation_xy_Transform_for_Images, x0=initial_parameters, args=(image_fixed, image_moving, metric), method='Nelder-Mead', tol=1e-2,
                       options={'maxiter': 1000, 'disp': True})

        gamma = res.x[0]
        rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
                                  [sin(gamma), cos(gamma), 0],
                                  [0, 0, 1]])

    elif constraints == 'xy':
        initial_parameters = [ini_param_rotation_real, ini_param_trans_x_real, ini_param_trans_y_real]
        res = minimize(minRigid_xy_Transform_for_Images, x0=initial_parameters, args=(image_fixed, image_moving, coord_center_rotation, metric), method='Nelder-Mead', tol=1e-2,
                       options={'maxiter': 1000, 'disp': True})

        # change result if input parameters are changed
        # tx_real = ini_param_trans + (ini_param_trans - tx) * 10
        gamma, tx, ty = res.x[0], real_optimization_parameters(res.x[1], initial_param=ini_param_trans_x, initial_step=initial_step), real_optimization_parameters(res.x[2], initial_param=ini_param_trans_y, initial_step=initial_step)
        rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
                                  [sin(gamma), cos(gamma), 0],
                                  [0, 0, 1]])
        translation_array = matrix([tx, ty, 0])

    return rotation_matrix, translation_array


def getRigidTransformFromLandmarks(points_fixed, points_moving, constraints='Tx_Ty_Tz_Rx_Ry_Rz', show=False):
    """
    Compute affine transformation to register landmarks
    :param points_fixed:
    :param points_moving:
    :param constraints:
    :param show:
    :return:
    """
    # TODO: check input constraints

    # list_constraints = [None, 'none', 'rigid', 'rigid-decomposed', 'xy', 'translation', 'translation-xy', 'rotation', 'rotation-xy', 'translation-scaling', 'translation-scaling-z', 'affine', 'Tx_Ty_Tz_Rx_Ry_Sz']
    # if constraints not in list_constraints:
    #     raise 'ERROR: the constraints must be one of those: ' + ', '.join(list_constraints[1:])

    # points = (points_fixed, points_moving, constraints)
    points_moving_reg = points_moving

    from scipy.optimize import minimize
    #
    # rotation_matrix = matrix([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    # translation_array = matrix([0.0, 0.0, 0.0])
    # points_moving_barycenter = [0.0, 0.0, 0.0]
    #
    # if constraints == 'rigid' or constraints == 'none' or constraints is None:
    #     initial_parameters = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    #     res = minimize(minRigidTransform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     alpha, beta, gamma, tx, ty, tz = res.x[0], res.x[1], res.x[2], res.x[3], res.x[4], res.x[5]
    #     rotation_matrix = matrix([[cos(alpha) * cos(beta), cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma),
    #                            cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma)],
    #                           [sin(alpha) * cos(beta), sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma),
    #                            sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma)],
    #                           [-sin(beta), cos(beta) * sin(gamma), cos(beta) * cos(gamma)]])
    #     translation_array = matrix([tx, ty, tz])
    #
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array
    #
    # elif constraints == 'rigid-decomposed':
    #     initial_parameters = [0.0, 0.0, 0.0]
    #     res = minimize(minTranslation_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     translation_array = matrix([res.x[0], res.x[1], res.x[2]])
    #     points_moving_reg_tmp = matrix(points_moving) + translation_array
    #
    #     points = (points_fixed, points_moving_reg_tmp)
    #
    #     initial_parameters = [0.0, 0.0, 0.0]
    #     res = minimize(minRotation_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     alpha, beta, gamma = res.x[0], res.x[1], res.x[2]
    #     rotation_matrix = matrix(
    #         [[cos(alpha) * cos(beta), cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma),
    #           cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma)],
    #          [sin(alpha) * cos(beta), sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma),
    #           sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma)],
    #          [-sin(beta), cos(beta) * sin(gamma), cos(beta) * cos(gamma)]])
    #     points_moving_barycenter = mean(points_moving_reg_tmp, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving_reg_tmp) - points_moving_barycenter).T).T + points_moving_barycenter)
    #
    # elif constraints == 'xy':
    #     initial_parameters = [0.0, 0.0, 0.0]
    #     res = minimize(minRigid_xy_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     gamma, tx, ty = res.x[0], res.x[1], res.x[2]
    #
    #     rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
    #                               [sin(gamma), cos(gamma), 0],
    #                               [0, 0, 1]])
    #     translation_array = matrix([tx, ty, 0])
    #
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array
    #
    # elif constraints == 'translation':
    #     initial_parameters = [0.0, 0.0, 0.0]
    #     res = minimize(minTranslation_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     translation_array = matrix([res.x[0], res.x[1], res.x[2]])
    #     points_moving_reg = matrix(points_moving) + translation_array
    #
    # elif constraints == 'translation-scaling':
    #     initial_parameters = [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]
    #     res = minimize(minTranslationScalingTransform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     scx, scy, scz, tx, ty, tz = res.x[0], res.x[1], res.x[2], res.x[3], res.x[4], res.x[5]
    #     rotation_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
    #     translation_array = matrix([tx, ty, tz])
    #
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array
    #
    # elif constraints == 'translation-scaling-z':
    #     initial_parameters = [1.0, 0.0, 0.0, 0.0]
    #     res = minimize(minTranslationScalingZTransform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     scz, tx, ty, tz = res.x[0], res.x[1], res.x[2], res.x[3]
    #     rotation_matrix = matrix([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, scz]])
    #     translation_array = matrix([tx, ty, tz])
    #
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (matrix(points_moving) - points_moving_barycenter).T).T +
    #                          points_moving_barycenter) + translation_array
    #
    # elif constraints == 'translation-xy':
    #     initial_parameters = [0.0, 0.0]
    #     res = minimize(minTranslation_xy_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     translation_array = matrix([res.x[0], res.x[1], 0.0])
    #     points_moving_reg = matrix(points_moving) + translation_array
    #
    # elif constraints == 'rotation':
    #     initial_parameters = [0.0, 0.0, 0.0]
    #     res = minimize(minRotation_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     alpha, beta, gamma = res.x[0], res.x[1], res.x[2]
    #     rotation_matrix = matrix(
    #         [[cos(alpha) * cos(beta), cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma),
    #           cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma)],
    #          [sin(alpha) * cos(beta), sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma),
    #           sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma)],
    #          [-sin(beta), cos(beta) * sin(gamma), cos(beta) * cos(gamma)]])
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter)
    #
    # elif constraints == 'rotation-xy':
    #     initial_parameters = [0.0]
    #     res = minimize(minRotation_xy_Transform, x0=initial_parameters, args=points, method='Nelder-Mead',
    #                    tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #
    #     gamma = res.x[0]
    #
    #     rotation_matrix = matrix([[cos(gamma), - sin(gamma), 0],
    #                               [sin(gamma), cos(gamma), 0],
    #                               [0, 0, 1]])
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotation_matrix * (
    #         matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter)
    #
    # elif constraints == 'affine':
    #     initial_parameters = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    #     res = minimize(minAffineTransform, x0=initial_parameters, args=points, method='Nelder-Mead', tol=1e-8, options={'xatol': 1e-8, 'fatol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    #     # res = minimize(minAffineTransform, x0=initial_parameters, args=points, method='Powell', tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 100000, 'maxfev': 100000, 'disp': show})
    #     # res = minimize(minAffineTransform, x0=initial_parameters, args=points, method='COBYLA', tol=1e-8, options={'tol': 1e-8, 'rhobeg': 0.1, 'maxiter': 100000, 'catol': 0, 'disp': show})
    #
    #     alpha, beta, gamma, tx, ty, tz, scx, scy, scz = res.x[0], res.x[1], res.x[2], res.x[3], res.x[4], res.x[5], \
    #                                                     res.x[6], res.x[7], res.x[8]
    #     scaling_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
    #     rotation_matrix = matrix(
    #         [[cos(alpha) * cos(beta), cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma),
    #           cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma)],
    #          [sin(alpha) * cos(beta), sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma),
    #           sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma)],
    #          [-sin(beta), cos(beta) * sin(gamma), cos(beta) * cos(gamma)]])
    #     rotsc_matrix = scaling_matrix * rotation_matrix
    #     translation_array = matrix([tx, ty, tz])
    #
    #     points_moving_barycenter = mean(points_moving, axis=0)
    #     points_moving_reg = ((rotsc_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array

    # elif constraints == 'Tx_Ty_Tz_Rx_Ry_Sz':

    # initialize default parameters
    init_param = [0, 0, 0, 0, 0, 0, 1, 1, 1]
    # initialize parameters for optimizer
    init_param_optimizer = []
    # initialize dictionary to relate constraints index to dof
    dict_dof = {'Tx': 0, 'Ty': 1, 'Tz': 2, 'Rx': 3, 'Ry': 4, 'Rz': 5, 'Sx': 6, 'Sy': 7, 'Sz': 8}
    # extract constraints
    list_constraints = constraints.split('_')
    # loop across constraints and build initial_parameters
    for i in range(len(list_constraints)):
        init_param_optimizer.append(init_param[dict_dof[list_constraints[i]]])

    # launch optimizer
    # res = minimize(minimize_transform, x0=init_param_optimizer, args=(points_fixed, points_moving, constraints), method='Nelder-Mead', tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 10000, 'maxfev': 10000, 'disp': show})
    res = minimize(minimize_transform, x0=init_param_optimizer, args=(points_fixed, points_moving, constraints), method='Powell', tol=1e-8, options={'xtol': 1e-8, 'ftol': 1e-8, 'maxiter': 100000, 'maxfev': 100000, 'disp': show})
    # res = minimize(minAffineTransform, x0=initial_parameters, args=points, method='COBYLA', tol=1e-8, options={'tol': 1e-8, 'rhobeg': 0.1, 'maxiter': 100000, 'catol': 0, 'disp': show})
    # loop across constraints and update dof
    dof = init_param
    for i in range(len(list_constraints)):
        dof[dict_dof[list_constraints[i]]] = res.x[i]
    # convert dof to more intuitive variables
    tx, ty, tz, alpha, beta, gamma, scx, scy, scz = dof[0], dof[1], dof[2], dof[3], dof[4], dof[5], dof[6], dof[7], dof[8]
    # convert results to intuitive variables
    # tx, ty, tz, alpha, beta, gamma, scx, scy, scz = res.x[0], res.x[1], res.x[2], res.x[3], res.x[4], res.x[5], res.x[6], res.x[7], res.x[8]
    # build translation matrix
    translation_array = matrix([tx, ty, tz])
    # build rotation matrix
    rotation_matrix = matrix([[cos(alpha)*cos(beta), cos(alpha)*sin(beta)*sin(gamma)-sin(alpha)*cos(gamma), cos(alpha)*sin(beta)*cos(gamma)+sin(alpha)*sin(gamma)],
                              [sin(alpha)*cos(beta), sin(alpha)*sin(beta)*sin(gamma)+cos(alpha)*cos(gamma), sin(alpha)*sin(beta)*cos(gamma)-cos(alpha)*sin(gamma)],
                              [-sin(beta), cos(beta)*sin(gamma), cos(beta)*cos(gamma)]])
    # build scaling matrix
    scaling_matrix = matrix([[scx, 0.0, 0.0], [0.0, scy, 0.0], [0.0, 0.0, scz]])
    # compute rotation+scaling matrix
    rotsc_matrix = scaling_matrix * rotation_matrix
    # compute center of mass from moving points (src)
    points_moving_barycenter = mean(points_moving, axis=0)
    # apply transformation to moving points (src)
    points_moving_reg = ((rotsc_matrix * (matrix(points_moving) - points_moving_barycenter).T).T + points_moving_barycenter) + translation_array

    if show:
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d import Axes3D

        print translation_array
        print rotation_matrix
        print points_moving_barycenter

        fig = plt.figure()
        ax = fig.gca(projection='3d')
        points_moving_matrix = matrix(points_moving)
        points_fixed_matrix = matrix(points_fixed)

        number_points = len(points_fixed)

        ax.scatter([points_fixed_matrix[i, 0] for i in range(0, number_points)],
                   [points_fixed_matrix[i, 1] for i in range(0, number_points)],
                   [points_fixed_matrix[i, 2] for i in range(0, number_points)], c='g', marker='+', s=500, label='dest')
        ax.scatter([points_moving_matrix[i, 0] for i in range(0, number_points)],
                   [points_moving_matrix[i, 1] for i in range(0, number_points)],
                   [points_moving_matrix[i, 2] for i in range(0, number_points)], c='r', label='src')
        ax.scatter([points_moving_reg[i, 0] for i in range(0, number_points)],
                   [points_moving_reg[i, 1] for i in range(0, number_points)],
                   [points_moving_reg[i, 2] for i in range(0, number_points)], c='b', label='src_reg')
        ax.set_xlabel('x')
        ax.set_ylabel('y')
        ax.set_zlabel('z')
        ax.set_aspect('auto')
        plt.legend()
        plt.show()

        fig2 = plt.figure()
        plt.plot(sse_results)
        plt.grid()
        plt.title('#Iterations: ' + str(res.nit) + ', #FuncEval: ' + str(res.nfev) + ', Error: ' + str(res.fun))
        plt.show()

    # transform numpy matrix to list structure because it is easier to handle after that
    points_moving_reg = points_moving_reg.tolist()

    return rotation_matrix, translation_array, points_moving_reg, points_moving_barycenter
