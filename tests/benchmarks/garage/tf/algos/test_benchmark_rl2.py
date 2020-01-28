"""This script creates a regression test over garage-TRPO and baselines-TRPO.

Unlike garage, baselines doesn't set max_path_length. It keeps steps the action
until it's done. So we introduced tests.wrappers.AutoStopEnv wrapper to set
done=True when it reaches max_path_length. We also need to change the
garage.tf.samplers.BatchSampler to smooth the reward curve.
"""
import datetime
import os.path as osp
import random
import numpy as np

import dowel
from dowel import logger as dowel_logger
import pytest
import tensorflow as tf

from garage.experiment import deterministic
from tests.fixtures import snapshot_config
import tests.helpers as Rh

from garage.envs import RL2Env
from garage.envs.half_cheetah_vel_env import HalfCheetahVelEnv
from garage.envs.half_cheetah_dir_env import HalfCheetahDirEnv
from garage.experiment.snapshotter import SnapshotConfig
from garage.np.baselines import LinearFeatureBaseline as GarageLinearFeatureBaseline
from garage.tf.algos import PPO as GaragePPO
from garage.tf.algos import RL2
from garage.tf.experiment import LocalTFRunner
from garage.tf.policies import GaussianGRUPolicy
from garage.tf.policies import GaussianLSTMPolicy
from garage.sampler.rl2_sampler import RL2Sampler

from maml_zoo.baselines.linear_baseline import LinearFeatureBaseline
from maml_zoo.envs.mujoco_envs.half_cheetah_rand_direc import HalfCheetahRandDirecEnv
from maml_zoo.envs.rl2_env import rl2env
from maml_zoo.algos.ppo import PPO
from maml_zoo.trainer import Trainer
from maml_zoo.samplers.maml_sampler import MAMLSampler
from maml_zoo.samplers.rl2_sample_processor import RL2SampleProcessor
from maml_zoo.policies.gaussian_rnn_policy import GaussianRNNPolicy
from maml_zoo.logger import logger

from metaworld.benchmarks import ML1
import os

os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

hyper_parameters = {
    'meta_batch_size': 50,
    'hidden_sizes': [64],
    'gae_lambda': 1,
    'discount': 0.99,
    'max_path_length': 150,
    'n_itr': 500,
    'rollout_per_task': 10,
    'positive_adv': False,
    'normalize_adv': True,
    'optimizer_lr': 1e-3,
    'lr_clip_range': 0.2,
    'optimizer_max_epochs': 5,
    'n_trials': 3,
    'cell_type': 'gru'
}

# If false, run ML else HalfCheetah
ML = True

class TestBenchmarkRL2:  # pylint: disable=too-few-public-methods
    """Compare benchmarks between garage and baselines."""

    @pytest.mark.huge
    def test_benchmark_rl2(self):  # pylint: disable=no-self-use
        """Compare benchmarks between garage and baselines."""
        if ML:
            envs = [
                ML1.get_train_tasks('push-v1'),
                ML1.get_train_tasks('reach-v1'),
                ML1.get_train_tasks('pick-place-v1')
            ]
            env_ids = ['ML1-push-v1', 'ML-reach-v1', 'ML1-pick-place-v1']
            # envs = [ML1.get_train_tasks('push-v1')]
            # env_ids = ['ML1-push-v1']
            # envs = [ML1.get_train_tasks('reach-v1')]
            # env_id = 'ML1-reach-v1'
            # envs = [ML1.get_train_tasks('pick-place-v1')]
            # env_id = 'ML1-pick-place-v1'
        else:
            envs = [HalfCheetahVelEnv(), HalfCheetahDirEnv()]
            env_ids = ['HalfCheetahVelEnv', 'HalfCheetahDirEnv']
            # envs = [HalfCheetahVelEnv()]
            # env_ids = ['HalfCheetahVelEnv']
            # envs = [HalfCheetahDirEnv()]
            # env_ids = ['HalfCheetahDirEnv']

        timestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S-%f')
        benchmark_dir = './data/local/benchmarks/rl2/%s/' % timestamp
        result_json = {}
        for i, env in enumerate(envs):
            seeds = random.sample(range(100), hyper_parameters['n_trials'])
            task_dir = osp.join(benchmark_dir, env_ids[i])
            plt_file = osp.join(benchmark_dir,
                                '{}_benchmark.png'.format(env_ids[i]))
            garage_tf_csvs = []
            promp_csvs = []

            for trial in range(hyper_parameters['n_trials']):
                seed = seeds[trial]
                trial_dir = task_dir + '/trial_%d_seed_%d' % (trial + 1, seed)
                garage_tf_dir = trial_dir + '/garage'
                promp_dir = trial_dir + '/promp'

                with tf.Graph().as_default():
                    env.reset()
                    garage_tf_csv = run_garage(env, seed, garage_tf_dir)

                with tf.Graph().as_default():
                    env.reset()
                    promp_csv = run_promp(env, seed, promp_dir)

                garage_tf_csvs.append(garage_tf_csv)
                promp_csvs.append(promp_csv)

            env.close()

            g_x = 'TotalEnvSteps'
            g_y = 'Evaluation/AverageReturn'
            g_y2 = 'SuccessRate'
            p_x = 'n_timesteps'
            p_y = 'train-AverageReturn'
            p_y2 = 'train-SuccessRate'


            Rh.relplot(g_csvs=garage_tf_csvs,
                       b_csvs=promp_csvs,
                       g_x=g_x,
                       g_y=g_y,
                       g_z='Garage',
                       b_x=p_x,
                       b_y=p_y,
                       b_z='ProMP',
                       trials=hyper_parameters['n_trials'],
                       seeds=seeds,
                       plt_file=plt_file,
                       env_id=env_ids[i],
                       x_label=g_x,
                       y_label=g_y)
            if ML:
                plt_file2 = osp.join(benchmark_dir,
                                '{}_benchmark_success_rate.png'.format(env_ids[i]))
                Rh.relplot(g_csvs=garage_tf_csvs,
                           b_csvs=promp_csvs,
                           g_x=g_x,
                           g_y=g_y2,
                           g_z='Garage',
                           b_x=p_x,
                           b_y=p_y2,
                           b_z='ProMP',
                           trials=hyper_parameters['n_trials'],
                           seeds=seeds,
                           plt_file=plt_file2,
                           env_id=env_ids[i],
                           x_label=g_x,
                           y_label=g_y2)


def run_garage(env, seed, log_dir):
    """Create garage Tensorflow PPO model and training.

    Args:
        env (dict): Environment of the task.
        seed (int): Random positive integer for the trial.
        log_dir (str): Log dir path.

    Returns:
        str: Path to output csv file

    """
    deterministic.set_seed(seed)
    snapshot_config = SnapshotConfig(snapshot_dir=log_dir,
                                     snapshot_mode='gap',
                                     snapshot_gap=10)
    with LocalTFRunner(snapshot_config) as runner:
        env = RL2Env(env)

        policy = GaussianGRUPolicy(
            hidden_dim=hyper_parameters['hidden_sizes'][0],
            env_spec=env.spec,
            state_include_action=False)

        baseline = GarageLinearFeatureBaseline(env_spec=env.spec)

        inner_algo = GaragePPO(
            env_spec=env.spec,
            policy=policy,
            baseline=baseline,
            max_path_length=hyper_parameters['max_path_length'] * hyper_parameters['rollout_per_task'],
            discount=hyper_parameters['discount'],
            gae_lambda=hyper_parameters['gae_lambda'],
            lr_clip_range=hyper_parameters['lr_clip_range'],
            optimizer_args=dict(
                max_epochs=hyper_parameters['optimizer_max_epochs'],
                tf_optimizer_args=dict(
                    learning_rate=hyper_parameters['optimizer_lr'],
                ),
            )
        )

        algo = RL2(
            policy=policy,
            inner_algo=inner_algo,
            max_path_length=hyper_parameters['max_path_length'])

        # Set up logger since we are not using run_experiment
        tabular_log_file = osp.join(log_dir, 'progress.csv')
        dowel_logger.add_output(dowel.CsvOutput(tabular_log_file))
        dowel_logger.add_output(dowel.StdOutput())
        dowel_logger.add_output(dowel.TensorBoardOutput(log_dir))

        runner.setup(algo, env, sampler_cls=RL2Sampler, sampler_args=dict(
            meta_batch_size=hyper_parameters['meta_batch_size'], n_envs=hyper_parameters['meta_batch_size']))
        runner.train(n_epochs=hyper_parameters['n_itr'],
            batch_size=hyper_parameters['meta_batch_size'] * hyper_parameters['rollout_per_task'] * hyper_parameters['max_path_length'])

        dowel_logger.remove_all()

        return tabular_log_file


def run_promp(env, seed, log_dir):
    deterministic.set_seed(seed)
    logger.configure(dir=log_dir, format_strs=['stdout', 'log', 'csv'],
                     snapshot_mode='gap', snapshot_gap=10)

    baseline = LinearFeatureBaseline()
    env = rl2env(env)
    obs_dim = np.prod(env.observation_space.shape) + np.prod(env.action_space.shape) + 1 + 1
    policy = GaussianRNNPolicy(
            name="meta-policy",
            obs_dim=obs_dim,
            action_dim=np.prod(env.action_space.shape),
            meta_batch_size=hyper_parameters['meta_batch_size'],
            hidden_sizes=hyper_parameters['hidden_sizes'],
            cell_type=hyper_parameters['cell_type']
        )

    sampler = MAMLSampler(
        env=env,
        policy=policy,
        rollouts_per_meta_task=hyper_parameters['rollout_per_task'],
        meta_batch_size=hyper_parameters['meta_batch_size'],
        max_path_length=hyper_parameters['max_path_length'],
        parallel=True,
        envs_per_task=1,
    )

    sample_processor = RL2SampleProcessor(
        baseline=baseline,
        discount=hyper_parameters['discount'],
        gae_lambda=hyper_parameters['gae_lambda'],
        normalize_adv=hyper_parameters['normalize_adv'],
        positive_adv=hyper_parameters['positive_adv'],
    )

    algo = PPO(
        policy=policy,
        learning_rate=hyper_parameters['optimizer_lr'],
        max_epochs=hyper_parameters['optimizer_max_epochs'],
        clip_eps=hyper_parameters['lr_clip_range']
    )

    trainer = Trainer(
        algo=algo,
        policy=policy,
        env=env,
        sampler=sampler,
        sample_processor=sample_processor,
        n_itr=hyper_parameters['n_itr'],
    )
    trainer.train()

    return osp.join(log_dir, 'progress.csv')
