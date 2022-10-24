'''
Modify from
https://github.com/Kai-46/nerfplusplus/blob/master/data_loader_split.py
'''
import os
import pdb
import glob
import scipy
import imageio
import numpy as np
import torch
from tqdm import tqdm
import json
from scipy.spatial.transform import Rotation as R
from comvog.common_data_loaders.load_llff import normalize
from comvog.trajectory_generators.waymo_traj import *

########################################################################################################################
# camera coordinate system: x-->right, y-->down, z-->scene (opencv/colmap convention)
# poses is camera-to-world
########################################################################################################################
def find_files(dir, exts):
    if os.path.isdir(dir):
        files_grabbed = []
        for ext in exts:
            files_grabbed.extend(glob.glob(os.path.join(dir, ext)))
        if len(files_grabbed) > 0:
            files_grabbed = sorted(files_grabbed)
        return files_grabbed
    else:
        return []


def waymo_load_img_list(split_dir, skip=1):
    # # camera parameters files
    # intrinsics_files = find_files('{}/intrinsics'.format(split_dir), exts=['*.txt'])
    # pose_files = find_files('{}/pose'.format(split_dir), exts=['*.txt'])

    # intrinsics_files = intrinsics_files[::skip]
    # pose_files = pose_files[::skip]
    # cam_cnt = len(pose_files)

    # img files
    img_files = find_files('{}'.format(split_dir), exts=['*.png', '*.jpg'])
    if len(img_files) > 0:
        img_files = img_files[::skip]
    else:
        raise RuntimeError(f"Cannot find image files at {split_dir}.")

    return img_files


def rerotate_poses(poses, render_poses):
    poses = np.copy(poses)
    centroid = poses[:,:3,3].mean(0)

    poses[:,:3,3] = poses[:,:3,3] - centroid

    # Find the minimum pca vector with minimum eigen value
    x = poses[:,:3,3]
    mu = x.mean(0)
    cov = np.cov((x-mu).T)
    ev , eig = np.linalg.eig(cov)
    cams_up = eig[:,np.argmin(ev)]
    if cams_up[1] < 0:
        cams_up = -cams_up

    # Find rotation matrix that align cams_up with [0,1,0]
    R = scipy.spatial.transform.Rotation.align_vectors(
            [[0,-1,0]], cams_up[None])[0].as_matrix()

    # Apply rotation and add back the centroid position
    poses[:,:3,:3] = R @ poses[:,:3,:3]
    poses[:,:3,[3]] = R @ poses[:,:3,[3]]
    poses[:,:3,3] = poses[:,:3,3] + centroid
    render_poses = np.copy(render_poses)
    render_poses[:,:3,3] = render_poses[:,:3,3] - centroid
    render_poses[:,:3,:3] = R @ render_poses[:,:3,:3]
    render_poses[:,:3,[3]] = R @ render_poses[:,:3,[3]]
    render_poses[:,:3,3] = render_poses[:,:3,3] + centroid
    return poses, render_poses


def sample_list_by_idx(one_list, idxs):
    # allow idxs to be out of range
    return [one_list[idx] for idx in idxs if idx < len(one_list)]
    
    
def sample_metadata_by_cam(metadata, cam_idx):
    for split in metadata:
        sample_idxs = []
        for idx, cam_id in enumerate(metadata[split]['cam_idx']):
            if cam_id == cam_idx:
                sample_idxs.append(idx)
        for one_k in metadata[split]:
            metadata[split][one_k] = sample_list_by_idx(metadata[split][one_k], sample_idxs)
    return metadata
    

def sample_metadata_by_idxs(metadata, sample_idxs):
    if sample_idxs is None:
        return metadata
    for split in metadata:
        for one_k in metadata[split]:
            metadata[split][one_k] = sample_list_by_idx(metadata[split][one_k], sample_idxs)
    return metadata


def sort_metadata_by_pos(metadata):
    for split in metadata:
        list_idxs = list(range(len(metadata[split]['position'])))
        sorted_idxs = sorted(zip(list_idxs, metadata[split]['position']), key=lambda row: (row[1][1], row[1][0]))
        sorted_idxs = [i for i, j in sorted_idxs]
        for one_k in metadata[split]:
            metadata[split][one_k] = sample_list_by_idx(metadata[split][one_k], sorted_idxs)
    return metadata


def normalize(x):
    return x / np.linalg.norm(x)


def viewmatrix(z, up, pos):
    vec2 = normalize(z)
    vec1_avg = up
    vec0 = normalize(np.cross(vec1_avg, vec2))
    vec1 = normalize(np.cross(vec2, vec0))
    m = np.stack([vec0, vec1, vec2, pos], 1)
    return m


def ptstocam(pts, c2w):
    tt = np.matmul(c2w[:3,:3].T, (pts-c2w[:3,3])[...,np.newaxis])[...,0]
    return tt


def poses_avg(poses):
    hwf = poses[0, :3, -1:]
    center = poses[:, :3, 3].mean(0)
    vec2 = normalize(poses[:, :3, 2].sum(0))
    up = poses[:, :3, 1].sum(0)
    c2w = np.concatenate([viewmatrix(vec2, up, center), hwf], 1)
    return c2w


def recenter_poses(poses):
    poses_ = poses+0
    bottom = np.reshape([0,0,0,1.], [1,4])
    c2w = poses_avg(poses)
    c2w = np.concatenate([c2w[:3,:4], bottom], -2)
    bottom = np.tile(np.reshape(bottom, [1,1,4]), [poses.shape[0],1,1])
    poses = np.concatenate([poses[:,:3,:4], bottom], -2)

    poses = np.linalg.inv(c2w) @ poses
    poses_[:,:3,:4] = poses[:,:3,:4]
    poses = poses_
    return poses


def find_most_freq_ele(one_list):
    most_freq_ele = max(set(one_list), key = one_list.count)
    freq_count = one_list.count(most_freq_ele)
    return most_freq_ele, freq_count

    
def load_waymo(args, data_cfg, ):
    load_img = False if args.program == "gen_trace" else True
    basedir = data_cfg.datadir
    with open(os.path.join(basedir, f'metadata.json'), 'r') as fp:
        metadata = json.load(fp)
    if 'sample_cam' in data_cfg:
        metadata = sample_metadata_by_cam(metadata, data_cfg['sample_cam'])
    if args.sample_num > 0:
        sample_idxs = list(range(0, args.sample_num * data_cfg['sample_interval'], data_cfg['sample_interval']))
        assert args.sample_num * data_cfg['sample_interval'] < len(metadata['train']['file_path']), \
            f"Not enough data to train with given sample interval: {data_cfg['sample_interval']}!"
    elif 'sample_idxs' in data_cfg:
        sample_idxs = data_cfg['sample_idxs']
    else:
        sample_idxs = None
    metadata = sort_metadata_by_pos(metadata)
    metadata = sample_metadata_by_idxs(metadata, sample_idxs)

    # The validation datasets are from the official val split, 
    # but the testing splits are hard-coded sequences (completely novel views)
    tr_cam_idx, val_cam_idx = metadata['train']['cam_idx'], metadata['val']['cam_idx']
    cam_idxs = tr_cam_idx + val_cam_idx
    train_pos, val_pos = metadata['train']['position'], metadata['val']['position']
    positions = train_pos + val_pos
    tr_im_path, val_im_path = metadata['train']['file_path'], metadata['val']['file_path']
    tr_c2w, val_c2w = metadata['train']['cam2world'], metadata['val']['cam2world']
    tr_K, val_K = metadata['train']['K'], metadata['val']['K']
    
    # Determine split id list
    i_split = [[], [], []]
    loop_id = 0
    for _ in tr_c2w:
        i_split[0].append(loop_id)
        loop_id += 1
    for _ in val_c2w:
        i_split[1].append(loop_id)
        loop_id += 1

    # Load camera poses
    poses = []
    for c2w in tr_c2w:
        poses.append(np.array(c2w).reshape(4,4))
    for c2w in val_c2w:
        poses.append(np.array(c2w).reshape(4,4))

    # Load images
    if not load_img:
        imgs = tr_im_path + val_im_path  # do not load all the images
    else:
        imgs = []
        print(f"Loading all the images to disk.")
        for path in tqdm(tr_im_path):
            imgs.append(imageio.imread(os.path.join(basedir, path)) / 255.)
        for path in tqdm(val_im_path):
            imgs.append(imageio.imread(os.path.join(basedir, path)) / 255.) 
        
    train_HW = np.array([[metadata['train']['height'][i], metadata['train']['width'][i]] 
                         for i in range(len(metadata['train']['height']))]).tolist()
    val_HW = np.array([[metadata['val']['height'][i], metadata['val']['width'][i]] 
                       for i in range(len(metadata['val']['height']))]).tolist()

    # Create the test split
    # te_c2w, test_HW, test_K, test_cam_idxs, test_pos = \
    #     gen_rotational_trajs(metadata, tr_c2w, train_HW, tr_K, tr_cam_idx, train_pos, 
    #                    rotate_angle=data_cfg.test_rotate_angle)
    te_c2w, test_HW, test_K, test_cam_idxs = \
        gen_straight_trajs(metadata, tr_c2w, train_HW, tr_K, tr_cam_idx, train_pos, 
                       rotate_angle=data_cfg.test_rotate_angle)
    for _ in te_c2w:
        i_split[2].append(loop_id)
        loop_id += 1
    for c2w in te_c2w:
        poses.append(np.array(c2w).reshape(4,4))
    
    # Bundle all the data
    all_K = np.array(tr_K + val_K + test_K)
    HW = np.array(train_HW + val_HW + test_HW)
    poses = np.stack(poses, 0)
    if load_img:
        imgs = np.stack(imgs)
    cam_idxs += test_cam_idxs
    # positions += test_pos
    return imgs, poses, HW, all_K, cam_idxs, i_split
    # return imgs, poses, HW, all_K, cam_idxs, i_split, positions


def inward_nearfar_heuristic(cam_o, ratio=0.05):
    dist = np.linalg.norm(cam_o[:,None] - cam_o, axis=-1)
    far = dist.max()  # could be too small to exist the scene bbox
                      # it is only used to determined scene bbox
                      # lib/dvgo use 1e9 as far
    near = far * ratio
    return near, far


def load_waymo_data(args, data_cfg):
    K, depths = None, None
    near_clip = None
    images, poses, HW, K, cam_idxs, i_split = load_waymo(args, data_cfg)
    print(f"Loaded waymo dataset.")
    i_train, i_val, i_test = i_split
    near_clip, far = inward_nearfar_heuristic(poses[i_train, :3, 3], ratio=0.02)  # not used too much in fact
    
    # load near and far parameters
    if "near_clip" in data_cfg:
        near_clip = data_cfg['near_clip']
    if 'near' in data_cfg:
        near = data_cfg['near']
    if 'far' in data_cfg:
        far = data_cfg['far']
    Ks = np.array(K)
    irregular_shape = False
    data_dict = dict(
        HW=HW, Ks=Ks, near=near, far=far, near_clip=near_clip,
        i_train=i_train, i_val=i_val, i_test=i_test,
        poses=poses, images=images, depths=depths, cam_idxs=cam_idxs, irregular_shape=irregular_shape
    )
    data_dict['poses'] = torch.tensor(data_dict['poses']).float()
    data_dict['images'] = torch.tensor(data_dict['images']).float()
    return data_dict