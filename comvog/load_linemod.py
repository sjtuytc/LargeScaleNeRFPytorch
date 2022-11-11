import pdb
import os
import pickle
import imageio
import numpy as np
import torch
import cv2
import math
import random
from comvog import utils
import open3d as o3d
from tqdm import tqdm
from scipy.spatial.transform import Rotation as R
from transforms3d.quaternions import mat2quat, quat2mat, qmult


trans_t = lambda t : torch.Tensor([
    [1,0,0,0],
    [0,1,0,0],
    [0,0,1,t],
    [0,0,0,1]]).float()

rot_phi = lambda phi : torch.Tensor([
    [1,0,0,0],
    [0,np.cos(phi),-np.sin(phi),0],
    [0,np.sin(phi), np.cos(phi),0],
    [0,0,0,1]]).float()

rot_theta = lambda th : torch.Tensor([
    [np.cos(th),0,-np.sin(th),0],
    [0,1,0,0],
    [np.sin(th),0, np.cos(th),0],
    [0,0,0,1]]).float()


def pose_spherical(theta, phi, radius):
    c2w = trans_t(radius)
    c2w = rot_phi(phi/180.*np.pi) @ c2w
    c2w = rot_theta(theta/180.*np.pi) @ c2w
    c2w = torch.Tensor(np.array([[-1,0,0,0],[0,0,1,0],[0,1,0,0],[0,0,0,1]])) @ c2w
    return c2w


def inward_nearfar_heuristic(cam_o, ratio=0.05):
    dist = np.linalg.norm(cam_o[:,None] - cam_o, axis=-1)
    far = dist.max()  # could be too small to exist the scene bbox
                      # it is only used to determined scene bbox
                      # lib/dvgo use 1e9 as far
    near = far * ratio
    return near, far


def visualize_2d_points(points_2d, bg_image, post_str=""):
    vis_img = np.zeros(bg_image.shape)
    points_2d = points_2d.astype(np.uint8)
    vis_img[points_2d[:, -1], points_2d[:, 0]] = 255 - vis_img[points_2d[:, -1], points_2d[:, 0]]
    imageio.imwrite(f'ori{post_str}.png', np.array(bg_image))
    imageio.imwrite(f'projected{post_str}.png', np.array(vis_img))
    imageio.imwrite(f'composed{post_str}.png', np.maximum(vis_img, bg_image))
    return


def get_projected_points(cam_pose, cam_k, obj_m, one_img=None, post_str=""):
    point_num = obj_m.shape[0]
    homo_points_3d = np.concatenate([obj_m, np.ones((point_num, 1))], axis=-1)
    batch_cam_pose = torch.tensor(cam_pose).unsqueeze(0).repeat(point_num, 1, 1)
    batch_cam_k = torch.tensor(cam_k).unsqueeze(0).repeat(point_num, 1, 1)
    homo_points_2d = torch.bmm(batch_cam_pose, torch.tensor(homo_points_3d).unsqueeze(-1))
    homo_points_2d = torch.bmm(batch_cam_k, homo_points_2d)
    points_2d = homo_points_2d.squeeze()
    points_2d = points_2d[:, :2] / points_2d[:, -1].unsqueeze(-1).repeat(1, 2)
    points_2d = points_2d.cpu().numpy()
    if one_img is not None:  # for visualization:
        visualize_2d_points(points_2d=points_2d, bg_image=one_img, post_str=post_str)
    return points_2d
    

def get_bbox_from_mask(label_img):
    contours = cv2.findNonZero(label_img)
    contours = contours.squeeze()
    xmin, xmax = np.min(contours[:, 0]), np.max(contours[:, 0])
    ymin, ymax = np.min(contours[:, 1]), np.max(contours[:, 1])
    return xmin, xmax, ymin, ymax
    

def apply_mask_on_img(one_img, label_img):
    one_img[..., 0] = one_img[..., 0] * label_img + 255 * (1 - label_img)
    one_img[..., 1] = one_img[..., 1] * label_img + 255 * (1 - label_img)
    one_img[..., 2] = one_img[..., 2] * label_img + 255 * (1 - label_img)
    return one_img


def get_most_bbox_size(seq_info, data_root):
    split = 'train'
    skip = 1
    imgs, poses, ks = [], [], []
    width_max = 0
    height_max = 0
    for idx, one_info in enumerate(seq_info[::skip]):
        one_k = one_info['K']
        ks.append(one_k)
        fname = os.path.join(data_root, 'LM6d_converted/LM6d_refine', one_info['rgb_observed_path'])
        one_img = imageio.imread(fname)
        label_fname = fname.replace('color', 'label')
        label_img = imageio.imread(label_fname)
        xmin, xmax, ymin, ymax = get_bbox_from_mask(label_img)
        if xmax - xmin > width_max:
            width_max = xmax - xmin
        if ymax - ymin > height_max:
            height_max = ymax - ymin
    return width_max, height_max


def gen_rotational_trajs(args, poses, test_num=100):
    init_c2w = poses[0]
    rotate_interval = 1
    all_poses = [init_c2w]
    for i in range(test_num - 1):
        cur_c2w = all_poses[-1].copy()
        rotate_r = R.from_euler('z', rotate_interval, degrees=True)
        cur_c2w[:3] = np.matmul(rotate_r.as_matrix(), cur_c2w[:3])
        all_poses.append(cur_c2w)
    all_poses = np.stack(all_poses, axis=0)
    return all_poses


def se3_q2m(se3_q):
    assert se3_q.size == 7
    se3_mx = np.zeros((3, 4))
    # quat = se3_q[0:4] / LA.norm(se3_q[0:4])
    quat = se3_q[:4]
    R = quat2mat(quat)
    se3_mx[:, :3] = R
    se3_mx[:, 3] = se3_q[4:]
    return se3_mx


def t_norm(pose):
    return np.linalg.norm(pose[:3, -1])
    

def uniform_three(kps):
    return_kps = np.float32([kps[0], kps[len(kps)//2], kps[-1]])
    return return_kps
    

def split_seq_info(seq_info, posecnn_results, val_num=20):
    posecnn_test_inds = [pose_r['image_idx'] for pose_r in posecnn_results]
    test_num = len(posecnn_test_inds)
    total_num = seq_info[-1]['index']
    train_num = int(total_num * 0.9)
    all_indexs = [i for i in range(total_num)]
    random.seed(0)
    train_indexs = random.sample(all_indexs, train_num)
    test_indexs = [ind for ind in all_indexs if ind not in train_indexs]
    train_info, val_info, test_info = [], [], []
    for one_info in seq_info:
        if int(one_info['index']) in test_indexs and int(one_info['index']) in posecnn_test_inds:
            assert posecnn_results[one_info['index']]['image_idx'] == one_info['index']
            one_info['pose_init_posecnn'] = se3_q2m(posecnn_results[one_info['index']]['pose'])
            test_info.append(one_info)
        else:
            train_info.append(one_info)
    val_info = train_info[:val_num]
    return train_info, val_info, test_info


def load_linemod_data(args, cfg, cal_size=False, vis_final=False):
    # load images and gts
    data_root = cfg.data.datadir
    seq_name = cfg.data.seq_name
    info_path = os.path.join(data_root, 'data_info/deepim', 'linemod_orig_deepim.info.train')
    with open(info_path, 'rb') as f:
        seq_info = pickle.load(f)[seq_name]

    # load model
    ply_path = os.path.join(data_root, 'models', seq_name, seq_name + ".xyz")
    obj_m = o3d.io.read_point_cloud(ply_path, format='xyz')
    obj_m = np.asarray(obj_m.points)
    
    # load pose initializations
    posecnn_results_p = os.path.join(data_root, 'init_poses/linemod_posecnn_results.pkl')
    with open(posecnn_results_p, 'rb') as f:
        posecnn_results=pickle.load(f)[seq_name]
    test_pose_path = os.path.join(data_root, 'init_poses/pvnet/pvnet_linemod_test.npy')
    pvnet_results=np.load(test_pose_path, allow_pickle=True).flat[0][seq_name]
    train_info, val_info, test_info = split_seq_info(seq_info, posecnn_results)
    all_imgs, all_poses, all_k = [], [], []
    counts = [0]
    if cal_size:
        print("Calculating box size ...")
        width_max, height_max = get_most_bbox_size(seq_info, data_root)
        print(f"Width max: {width_max}, height max: {height_max} ...")
    else:  # not tested when width_max != height_max
        width_max, height_max = cfg.data.width_max, cfg.data.height_max
    print("Preparing the training / val / test poses and images ...")
    for split in ['train', 'val', 'test']:
        if split == 'train':
            split_seq = train_info
        elif split == 'val':
            split_seq = val_info
        else:
            split_seq = test_info
        imgs, poses, ks = [], [], []
        for idx, one_info in enumerate(tqdm(split_seq)):
            one_k = one_info['K']
            fname = os.path.join(data_root, 'LM6d_converted/LM6d_refine', one_info['rgb_observed_path'])
            one_img = imageio.imread(fname)
            label_fname = fname.replace('color', 'label')
            label_img = imageio.imread(label_fname)
            mask_img = label_img / label_img.max()
            one_img = apply_mask_on_img(one_img, mask_img)
            xmin, xmax, ymin, ymax = get_bbox_from_mask(label_img)
            # transfer the obj to the center of image and crop.
            obj_width, obj_height = xmax - xmin, ymax - ymin
            cropped_img = one_img[ymin:ymax, xmin:xmax, :]
            centered_img = np.ones((height_max, width_max, 3), dtype='uint8') * 255  # white bg by convention
            x_start, y_start = width_max // 2 - obj_width // 2, height_max // 2 - obj_height // 2
            centered_img[y_start: y_start+obj_height, x_start:x_start+obj_width, :] = cropped_img
            # making the K work for the recenter
            x_ratio = width_max // 2 / one_k[0][-1]
            y_ratio = height_max // 2 / one_k[1][-1]
            recenter_k = one_k.copy()
            recenter_k[0][-1] = one_k[0][-1] * x_ratio
            recenter_k[1][-1] = one_k[1][-1] * y_ratio
            ks.append(recenter_k)
            # making the pose work for the recenter
            points_2d = get_projected_points(one_info['gt_pose'], one_info['K'], obj_m, one_img=None)
            points_2d[:, 0] = points_2d[:, 0] - xmin + x_start
            points_2d[:, 1] = points_2d[:, 1] - ymin + y_start
            _, rvec, tvec, inlier = cv2.solvePnPRansac(obj_m, points_2d, recenter_k, None)
            r_matrix, _ = cv2.Rodrigues(rvec)
            recenter_pose = np.concatenate([r_matrix, tvec], axis=-1)
            # transform t_norm to 1.0, transform image and pose together
            points_2d_before_norm = get_projected_points(recenter_pose, recenter_k, obj_m, one_img=None, post_str="")
            recenter_pose[:3, -1] = recenter_pose[:3, -1] / np.linalg.norm(recenter_pose[:3, -1])
            points_2d_norm = get_projected_points(recenter_pose, recenter_k, obj_m, one_img=None, post_str="")
            affine_m = cv2.getAffineTransform(uniform_three(points_2d_before_norm), uniform_three(points_2d_norm))
            center_normed = cv2.warpAffine(centered_img, affine_m, (centered_img.shape[1], centered_img.shape[0]), borderMode=cv2.BORDER_CONSTANT,
                                           borderValue=(255, 255, 255))
            if vis_final:
                get_projected_points(recenter_pose, recenter_k, obj_m, one_img=center_normed, post_str="final")
            imgs.append(center_normed)
            # form final poses
            last_row = [0., 0., 0., 1.0]
            recenter_pose = np.vstack([recenter_pose, last_row])
            # from object pose to camera pose
            cam_pose = np.linalg.inv(recenter_pose)
            # transfer from outward to inward
            cam_pose[:3, -1] = - cam_pose[:3, -1]
            poses.append(cam_pose)
            
        imgs = (np.array(imgs) / 255.).astype(np.float32) # keep all 4 channels (RGBA)
        poses = np.array(poses).astype(np.float32)
        ks = np.array(ks).astype(np.float32)
        counts.append(counts[-1] + imgs.shape[0])
        all_imgs.append(imgs)
        all_poses.append(poses)
        all_k.append(ks)
    print("Finished preparing the training / val / test poses and images !")
    i_split = [np.arange(counts[i], counts[i+1]) for i in range(3)]
    images = np.concatenate(all_imgs, 0)
    poses = np.concatenate(all_poses, 0)
    ks = np.concatenate(all_k, 0)
    # render_poses = torch.stack([pose_spherical(angle, -30.0, 1.0) for angle in np.linspace(-180,180,160+1)[:-1]], 0)
    render_poses = gen_rotational_trajs(args, poses)
    
    i_train, i_val, i_test = i_split
    near, far = 0., 6.
    # if images.shape[-1] == 4:
    #     if args.white_bkgd:
    #         images = images[...,:3]*images[...,-1:] + (1.-images[...,-1:])
    #     else:
    #         images = images[...,:3]*images[...,-1:]
    
    # Cast intrinsics to right types
    HW = np.array([im.shape[:2] for im in images])
    irregular_shape = (images.dtype is np.dtype('object'))
    render_poses = render_poses[...,:4]
    near_clip, far = inward_nearfar_heuristic(poses[i_train, :3, 3], ratio=0.02)
    data_dict = dict(HW=HW, Ks=ks,
        near=near, far=far, near_clip=near_clip,
        i_train=i_train, i_val=i_val, i_test=i_test,
        poses=torch.tensor(poses), render_poses=torch.tensor(render_poses),
        images=torch.tensor(images), depths=None,
        irregular_shape=irregular_shape,
    )
    return data_dict