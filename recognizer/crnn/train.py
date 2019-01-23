#coding=utf-8

import Config
import random
import os
import numpy as np
import torch
from warpctc_pytorch import CTCLoss
import torch.backends.cudnn as cudnn
import lib.dataset
import lib.convert
import lib.utility
from torch.autograd import Variable
import Net.net as Net
import torch.optim as optim
import evaluate


def trainBatch(net, criterion, optimizer, train_iter, converter, image, text, length):
    data = train_iter.next()
    cpu_images, cpu_texts = data
    batch_size = cpu_images.size(0)
    lib.dataset.loadData(image, cpu_images)
    t, l = converter.encode(cpu_texts)
    lib.dataset.loadData(text, t)
    lib.dataset.loadData(length, l)

    preds = net(image)
    #print("preds.size=%s" % preds.size)
    preds_size = Variable(torch.IntTensor([preds.size(0)] * batch_size))  # preds.size(0)=w=22
    cost = criterion(preds, text, preds_size, length) / batch_size  # length= a list that contains the len of text label in a batch
    net.zero_grad()
    cost.backward()
    optimizer.step()
    return cost


if __name__ == '__main__':
    if not os.path.exists(Config.model_dir):
        os.mkdir(Config.model_dir)

    random.seed(Config.random_seed)
    np.random.seed(Config.random_seed)
    torch.manual_seed(Config.random_seed)

    os.environ['CUDA_VISIBLE_DEVICES'] = Config.gpu_id

    cudnn.benchmark = True
    if torch.cuda.is_available() and Config.using_cuda:
        cuda = True
        print('Using cuda')
    else:
        cuda = False
        print('Using cpu mode')

    train_dataset = lib.dataset.lmdbDataset(root=Config.train_data)
    test_dataset = lib.dataset.lmdbDataset(root=Config.test_data)
    assert train_dataset

    # images will be resize to 32*160
    train_loader = torch.utils.data.DataLoader(
        train_dataset, batch_size=Config.batch_size,
        shuffle=True,
        num_workers=int(Config.data_worker),
        collate_fn=lib.dataset.alignCollate(imgH=Config.img_height, imgW=Config.img_width))

    n_class = len(Config.alphabet) + 1  # for python3
    #n_class = len(Config.alphabet.decode('utf-8')) + 1  # for python2
    print("alphabet class num is %s" % n_class)

    converter = lib.convert.StrConverter(Config.alphabet)
    # print(converter.dict)

    criterion = CTCLoss()

    net = Net.CRNN(n_class)
    print(net)

    net.apply(lib.utility.weights_init)

    image = torch.FloatTensor(Config.batch_size, 3, Config.img_height, Config.img_width)
    text = torch.IntTensor(Config.batch_size * 5)
    length = torch.IntTensor(Config.batch_size)

    if cuda:
        net.cuda()
        image = image.cuda()
        criterion = criterion.cuda()

    # image = Variable(image)
    # text = Variable(text)
    # length = Variable(length)

    loss_avg = lib.utility.averager()

    optimizer = optim.Adadelta(net.parameters(), lr=Config.lr)

    for epoch in range(Config.epoch):
        train_iter = iter(train_loader)
        i = 0
        while i < len(train_loader):
            for p in net.parameters():
                p.requires_grad = True
            net.train()

            cost = trainBatch(net, criterion, optimizer, train_iter, converter, image, text, length)
            loss_avg.add(cost)
            i += 1

            if i % Config.display_interval == 0:
                print('[%d/%d][%d/%d] Loss: %f' %
                      (epoch, Config.epoch, i, len(train_loader), loss_avg.val()))
                loss_avg.reset()

            if i % Config.test_interval == 0:
                evaluate.val(net, test_dataset, criterion, converter, image, text, length,
                             max_iter=Config.test_batch_num)

            # do checkpointing
            if i % Config.save_interval == 0:
                torch.save(
                    net.state_dict(), '{0}/netCRNN_{1}_{2}.pth'.format(Config.model_dir, epoch, i))
