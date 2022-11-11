# linemod train
python run_comvog.py --program train --config comvog/configs/linemod/ape.py --render_train --render_test --render_video --exp_id 9
# linemod test
python run_comvog.py --program tune_pose --config comvog/configs/linemod/ape.py --exp_id 1
# waymo dataset
python run_comvog.py --program train --config comvog/configs/waymo/waymo_no_block.py --render_video --exp_id 30
# tanks and temples
python run_comvog.py --program train --config comvog/configs/tankstemple_unbounded/playground_single.py --num_per_block -1 --render_train --render_test --render_video --exp_id 53
# original DVGOv2 training
# python run_comvog.py --program train --config comvog/configs/waymo/block_0_tt.py
# -----------------------------------------------------------------------
# building
python run_comvog.py --program train --config comvog/configs/mega/building_no_block.py --sample_num 300 --render_train --render_video --exp_id 50
python run_comvog.py --program render --config comvog/configs/mega/building.py --sample_num 100 --render_train --exp_id 12 --num_per_block 5  # for render
# rubble
python run_comvog.py --program train --config comvog/configs/mega/rubble.py --sample_num 100 --render_train --num_per_block 5 --exp_id 4  # for train
python run_comvog.py --program train --config comvog/configs/mega/rubble.py --sample_num 10 --render_train --num_per_block 5 --exp_id 4  # for debug
python run_comvog.py --program render --config comvog/configs/mega/rubble.py --sample_num 100 --render_train --num_per_block 5 --exp_id 4  # for render
# quad
python run_comvog.py --program train --config comvog/configs/mega/quad.py --sample_num 100 --render_train --num_per_block 5 --exp_id 1
# lego
python run_comvog.py --program train --config comvog/configs/nerf/lego.py --render_test --eval_ssim --eval_lpips_vgg --exp_id 8