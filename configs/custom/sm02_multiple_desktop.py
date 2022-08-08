_base_ = './default_forward_facing.py'

expname = 'sm02_multiple_desktop'

data = dict(
    datadir='./data/sm02_multiple_desktop/dense',
    factor=2,
    movie_render_kwargs={
        'scale_r': 0.5,
        'scale_f': 1.0,
        'zrate': 1.0,
        'zdelta': 0.5,
    }
)
