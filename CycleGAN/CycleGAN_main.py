import time, pickle, argparse, network, utils, itertools
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
from torchvision import transforms
from torch.autograd import Variable
import test
import train

parser = argparse.ArgumentParser()
parser.add_argument('--dataset', required=False, default='apple2orange',  help='the name of train set')
parser.add_argument('--train_subfolder', required=False, default='train',  help='the subfolder name of train set')
parser.add_argument('--test_subfolder', required=False, default='test',  help='the subfolder name of test set')
parser.add_argument('--input_ngc', type=int, default=3, help='number of input channel for generator')
parser.add_argument('--output_ngc', type=int, default=3, help='number of output channel for generator')
parser.add_argument('--input_ndc', type=int, default=3, help='number of input channel for discriminator')
parser.add_argument('--output_ndc', type=int, default=1, help='number of output channel for discriminator')
parser.add_argument('--batch_size', type=int, default=1, help='batch size')
parser.add_argument('--ngf', type=int, default=32)
parser.add_argument('--ndf', type=int, default=64)
parser.add_argument('--nb', type=int, default=9, help='the number of resnet block layer for generator')
parser.add_argument('--input_size', type=int, default=256, help='input size')
parser.add_argument('--resize_scale', type=int, default=286, help='resize scale (0 is false)')
parser.add_argument('--crop', type=bool, default=True, help='random crop True or False')
parser.add_argument('--fliplr', type=bool, default=True, help='random fliplr True or False')
parser.add_argument('--train_epoch', type=int, default=200, help='train epochs num')
parser.add_argument('--decay_epoch', type=int, default=100, help='learning rate decay start epoch num')
parser.add_argument('--lrD', type=float, default=0.0002, help='learning rate for discriminator')
parser.add_argument('--lrG', type=float, default=0.0002, help='learning rate for generator')
parser.add_argument('--lambdaA', type=float, default=10, help='lambdaA for cycle loss')
parser.add_argument('--lambdaB', type=float, default=10, help='lambdaB for cycle loss')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for Adam optimizer')
parser.add_argument('--beta2', type=float, default=0.999, help='beta2 for Adam optimizer')
parser.add_argument('--save_root', required=False, default='results', help='results save path')
parser.add_argument('--cuda', type=bool, default=True, help='use GPU computation')
opt = parser.parse_args()
print('------------ Arguments -------------')
for k, v in sorted(vars(opt).items()):
    print('%s = %s' % (str(k), str(v)))
print('-------------- End ----------------')

# results save path
root, model = utils.filepath_check_and_initialize(opt.dataset, opt.save_root)

# data_loader
transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
])
train_loader_A = utils.data_load('data/' + opt.dataset, opt.train_subfolder + 'A', transform, opt.batch_size, shuffle=True)
train_loader_B = utils.data_load('data/' + opt.dataset, opt.train_subfolder + 'B', transform, opt.batch_size, shuffle=True)
test_loader_A = utils.data_load('data/' + opt.dataset, opt.test_subfolder + 'A', transform, opt.batch_size, shuffle=False)
test_loader_B = utils.data_load('data/' + opt.dataset, opt.test_subfolder + 'B', transform, opt.batch_size, shuffle=False)

# network
# initialize generators and discriminators
G_A, G_B = network.initialize_generators(opt.input_ngc, opt.output_ngc, opt.ngf, opt.nb, opt.cuda)
D_A, D_B = network.initialize_discriminators(opt.input_ndc, opt.output_ndc, opt.ndf, opt.cuda)

print('---------- Networks initialized -------------')
utils.print_network(G_A)
utils.print_network(G_B)
utils.print_network(D_A)
utils.print_network(D_B)
print('-----------------------------------------------')

# loss
BCE_loss = nn.BCELoss().cuda()
MSE_loss = nn.MSELoss().cuda()
L1_loss = nn.L1Loss().cuda()

# Adam optimizer
G_optimizer = optim.Adam(itertools.chain(G_A.parameters(), G_B.parameters()), lr=opt.lrG, betas=(opt.beta1, opt.beta2))
D_A_optimizer = optim.Adam(D_A.parameters(), lr=opt.lrD, betas=(opt.beta1, opt.beta2))
D_B_optimizer = optim.Adam(D_B.parameters(), lr=opt.lrD, betas=(opt.beta1, opt.beta2))

# image store
fakeA_store = utils.ImagePool(50)
fakeB_store = utils.ImagePool(50)

train_hist = utils.train_histogram_initialize()

print('**************************start training!**************************')
start_time = time.time()
for epoch in range(opt.train_epoch):
    D_A_losses = []
    D_B_losses = []
    G_A_losses = []
    G_B_losses = []
    A_cycle_losses = []
    B_cycle_losses = []
    epoch_start_time = time.time()
    num_iter = 0
    if (epoch+1) > opt.decay_epoch:
        D_A_optimizer.param_groups[0]['lr'] -= opt.lrD / (opt.train_epoch - opt.decay_epoch)
        D_B_optimizer.param_groups[0]['lr'] -= opt.lrD / (opt.train_epoch - opt.decay_epoch)
        G_optimizer.param_groups[0]['lr'] -= opt.lrG / (opt.train_epoch - opt.decay_epoch)

    for (realA, _), (realB, _) in zip(train_loader_A, train_loader_B):
        if opt.resize_scale:
            realA = utils.imgs_resize(realA, opt.resize_scale)
            realB = utils.imgs_resize(realB, opt.resize_scale)

        if opt.crop:
            realA = utils.random_crop(realA, opt.input_size)
            realB = utils.random_crop(realB, opt.input_size)

        if opt.fliplr:
            realA = utils.random_fliplr(realA)
            realB = utils.random_fliplr(realB)

        realA, realB = Variable(realA.cuda()), Variable(realB.cuda())

        # train generator G
        G_optimizer.zero_grad()

        # generate real A to fake B; D_A(G_A(A))
        fakeB = G_A(realA)
        D_A_result = D_A(fakeB)
        G_A_loss = MSE_loss(D_A_result, Variable(torch.ones(D_A_result.size()).cuda()))

        # reconstruct fake B to rec A; G_B(G_A(A))
        recA = G_B(fakeB)
        A_cycle_loss = L1_loss(recA, realA) * opt.lambdaA

        # generate real B to fake A; D_A(G_B(B))
        fakeA = G_B(realB)
        D_B_result = D_B(fakeA)
        G_B_loss = MSE_loss(D_B_result, Variable(torch.ones(D_B_result.size()).cuda()))

        # reconstruct fake A to rec B G_A(G_B(B))
        recB = G_A(fakeA)
        B_cycle_loss = L1_loss(recB, realB) * opt.lambdaB

        G_loss = G_A_loss + G_B_loss + A_cycle_loss + B_cycle_loss
        G_loss.backward()
        G_optimizer.step()
        
        train_hist['G_A_losses'].append(G_A_loss.data)
        train_hist['G_B_losses'].append(G_B_loss.data)
        train_hist['A_cycle_losses'].append(A_cycle_loss.data)
        train_hist['B_cycle_losses'].append(B_cycle_loss.data)

        G_A_losses.append(G_A_loss.data)
        G_B_losses.append(G_B_loss.data)
        A_cycle_losses.append(A_cycle_loss.data)
        B_cycle_losses.append(B_cycle_loss.data)

        # train discriminator D_A
        D_A_optimizer.zero_grad()

        D_A_real = D_A(realB)
        D_A_real_loss = MSE_loss(D_A_real, Variable(torch.ones(D_A_real.size()).cuda()))

        fakeB = fakeB_store.query(fakeB)
        D_A_fake = D_A(fakeB)
        D_A_fake_loss = MSE_loss(D_A_fake, Variable(torch.zeros(D_A_fake.size()).cuda()))

        D_A_loss = (D_A_real_loss + D_A_fake_loss) * 0.5
        D_A_loss.backward()
        D_A_optimizer.step()

        train_hist['D_A_losses'].append(D_A_loss.data)
        D_A_losses.append(D_A_loss.data)

        # train discriminator D_B
        D_B_optimizer.zero_grad()

        D_B_real = D_B(realA)
        D_B_real_loss = MSE_loss(D_B_real, Variable(torch.ones(D_B_real.size()).cuda()))

        fakeA = fakeA_store.query(fakeA)
        D_B_fake = D_B(fakeA)
        D_B_fake_loss = MSE_loss(D_B_fake, Variable(torch.zeros(D_B_fake.size()).cuda()))

        D_B_loss = (D_B_real_loss + D_B_fake_loss) * 0.5
        D_B_loss.backward()
        D_B_optimizer.step()

        train_hist['D_B_losses'].append(D_B_loss.data)
        D_B_losses.append(D_B_loss.data)

        num_iter += 1

    epoch_end_time = time.time()
    per_epoch_ptime = epoch_end_time - epoch_start_time
    train_hist['per_epoch_ptimes'].append(per_epoch_ptime)
    print(
    '[%d/%d] - ptime: %.2f, loss_D_A: %.3f, loss_D_B: %.3f, loss_G_A: %.3f, loss_G_B: %.3f, loss_A_cycle: %.3f, loss_B_cycle: %.3f' % (
        (epoch + 1), opt.train_epoch, per_epoch_ptime, torch.mean(torch.FloatTensor(D_A_losses)),
        torch.mean(torch.FloatTensor(D_B_losses)), torch.mean(torch.FloatTensor(G_A_losses)),
        torch.mean(torch.FloatTensor(G_B_losses)), torch.mean(torch.FloatTensor(A_cycle_losses)),
        torch.mean(torch.FloatTensor(B_cycle_losses))))


    if (epoch+1) % 10 == 0:
        test.test_results_network(test_loader_A, test_loader_B, G_A, G_B, opt.dataset)
    else:
        train.train_results_network(train_loader_A, train_loader_B, G_A, G_B, opt.dataset)
        
end_time = time.time()
total_time = end_time - start_time
train_hist['total_time'].append(total_time)

print("Avg one epoch passing time: %.2f, total %d epochs passing time: %.2f" % (torch.mean(torch.FloatTensor(train_hist['per_epoch_ptimes'])), opt.train_epoch, total_time))
print("Training finish!... save training results")
torch.save(G_A.state_dict(), root + model + 'generatorA_param.pkl')
torch.save(G_B.state_dict(), root + model + 'generatorB_param.pkl')
torch.save(D_A.state_dict(), root + model + 'discriminatorA_param.pkl')
torch.save(D_B.state_dict(), root + model + 'discriminatorB_param.pkl')
with open(root + model + 'train_hist.pkl', 'wb') as f:
    pickle.dump(train_hist, f)

utils.show_train_hist(train_hist, save=True, path=root + model + 'train_hist.png')
