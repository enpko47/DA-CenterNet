from models import centernet
from utils import dataset
from utils import voc0712
from utils import tool
from evaluation import metric

import numpy as np
import torch
import cv2

import argparse
import os
import shutil

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='CenterNet Detection')
    parser.add_argument('--batch-size', default=64, type=int,
                        help='Batch size for training')

    parser.add_argument('--img-w', default=512, type=int)
    parser.add_argument('--img-h', default=512, type=int)

    parser.add_argument('--weights', type=str, default="", help='load weights to resume training')
    parser.add_argument('--root', default="./dataset/VOCDevkit", help='Location of dataset directory')
    parser.add_argument('--dataset-name', type=str, default="voc")
    parser.add_argument('--num-workers', default=8, type=int, help='Number of workers used in dataloading')
    parser.add_argument('--flip', action='store_true')
    
    opt = parser.parse_args()
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = centernet.CenterNet(pretrained_backbone=True)
    if opt.weights is not None:
        chkpt = torch.load(opt.weights, map_location=device)
        model.load_state_dict(chkpt['model_state_dict'], strict=False)
    model.eval()
    model = model.to(device=device)
    
    test_set = dataset.DetectionDataset(root=opt.root, 
                                        dataset_name=opt.dataset_name, 
                                        set="test",
                                        img_w=opt.img_w, 
                                        img_h=opt.img_h,
                                        keep_ratio=True)
    
    test_set_loader = torch.utils.data.DataLoader(test_set, 
                                                  opt.batch_size,
                                                  num_workers=opt.num_workers,
                                                  shuffle=False,
                                                  collate_fn=dataset.collate_fn,
                                                  pin_memory=True,
                                                  drop_last=False)
    
    tool.mkdir(dir="gt", remove_existing_dir=True)
    tool.mkdir(dir="pred", remove_existing_dir=True)
        
    gt_bboxes_batch = []
    class_tp_fp_score_batch = []
    with torch.no_grad():

        for batch_data in test_set_loader:
            batch_img = batch_data["img"].to(device)
            batch_label = batch_data["label"]
            batch_idx = batch_data["idx"]
            batch_org_img_shape = batch_data["org_img_shape"]
            batch_padded_ltrb = batch_data["padded_ltrb"]

            batch_output = model(batch_img, flip=opt.flip)
            batch_output = model.post_processing(batch_output, batch_org_img_shape, batch_padded_ltrb, confidence_threshold=1e-2)
            
            for i in range(len(batch_img)):
                idx = batch_idx[i] # data index
                
                org_img_shape = batch_org_img_shape[i] # (w, h)
                padded_ltrb = batch_padded_ltrb[i]
                
                target_bboxes = batch_label[i]#.numpy()

                pred_bboxes = batch_output[i]
                target_bboxes = tool.reconstruct_bboxes(normalized_bboxes=target_bboxes,
                                                        resized_img_shape=(model.img_w, model.img_h),
                                                        padded_ltrb=padded_ltrb,
                                                        org_img_shape=org_img_shape)
                target_bboxes = target_bboxes.numpy()

                gt_bboxes_batch.append(target_bboxes)

                img = cv2.imread(test_set.dataset.images_path[idx])
                
                img_file = os.path.basename(test_set.dataset.images_path[idx])
                txt_file = img_file.replace(".jpg", ".txt")
                
                gt_txt_file = os.path.join("gt", txt_file)
                pred_txt_file = os.path.join("pred", txt_file)
                
                with open(gt_txt_file, "w") as f:
                    for target_bbox in target_bboxes:
                        c = int(target_bbox[0])
                        l = (target_bbox[1] - target_bbox[3] / 2.)
                        r = (target_bbox[1] + target_bbox[3] / 2.)

                        t = (target_bbox[2] - target_bbox[4] / 2.)
                        b = (target_bbox[2] + target_bbox[4] / 2.)
                        
                        f.write(f"{voc0712.CLASSES[c]} {l} {t} {r} {b}\n")
                        
                with open(pred_txt_file, "w") as f:
                    if pred_bboxes["num_detected_bboxes"] > 0:
                        pred_bboxes = np.concatenate([pred_bboxes["class"].reshape(-1, 1), 
                                                    pred_bboxes["position"].reshape(-1, 4),
                                                    pred_bboxes["confidence"].reshape(-1, 1)], axis=1)
            
                        class_tp_fp_score = metric.measure_tpfp(pred_bboxes, target_bboxes, 0.5, bbox_format='cxcywh')
                        class_tp_fp_score_batch.append(class_tp_fp_score)
                        for pred_bbox in pred_bboxes:
                            c = int(pred_bbox[0])
                            
                            l = (pred_bbox[1] - pred_bbox[3] / 2.)
                            r = (pred_bbox[1] + pred_bbox[3] / 2.)

                            t = (pred_bbox[2] - pred_bbox[4] / 2.)
                            b = (pred_bbox[2] + pred_bbox[4] / 2.)
                            
                            confidence = pred_bbox[5]
                            
                            f.write(f"{voc0712.CLASSES[c]} {confidence} {l} {t} {r} {b}\n")

                            cv2.rectangle(img=img, pt1=(int(l), int(t)), pt2=(int(r), int(b)), color=(255, 0, 0), thickness=3)

                    cv2.imshow('img', img)
                    cv2.waitKey(1)
                
        mean_ap = metric.compute_map(class_tp_fp_score_batch, gt_bboxes_batch, num_classes=model.num_classes)
        print(mean_ap)