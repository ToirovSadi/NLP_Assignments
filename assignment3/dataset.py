import torch
from torch.utils.data import Dataset

from datasets import load_dataset
from transformers import AutoTokenizer

class NERDataset(Dataset):
    def __init__(self, tokenizer="DeepPavlov/rubert-base-cased", mode='train', max_length=128, return_all=False):
        self.mode = mode
        self.max_length = max_length
        self.return_all = return_all

        # get the dataset
        self.dataset_link = 'iluvvatar/RuNNE'
        self.dataset = load_dataset(self.dataset_link, trust_remote_code=True)
        self.dataset = self.dataset[mode]

        # get the tokenizer
        if type(tokenizer) is str:
            self.tokenizer = AutoTokenizer.from_pretrained(tokenizer)
        else:
            self.tokenizer = tokenizer

        # get the tags from the 'ner_tags.txt' and assign them id
        with open('ner_tags.txt', 'r') as f:
            tags = f.read().split('\n')
        self.tags2id = dict()

        for i, tag in enumerate(tags):
            self.tags2id['S-' + tag] = 4 * i + 1
            self.tags2id['B-' + tag] = 4 * i + 2
            self.tags2id['I-' + tag] = 4 * i + 3
            self.tags2id['E-' + tag] = 4 * i + 4
        self.tags2id['O'] = 0

        self.id2tags = {v: k for k, v in self.tags2id.items()}

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, index):
        # get the text
        text = self.dataset['text'][index]
        t = self.tokenizer(text, return_offsets_mapping=True, return_special_tokens_mask=True)
        
        token_type_ids = t['token_type_ids']
        tokens = self.tokenizer.convert_ids_to_tokens(t['input_ids'])
        offsets, special_tokens_mask = t['offset_mapping'], t['special_tokens_mask']
        if self.mode == 'dev':
            return self._truncate_output((
                t['input_ids'],
                token_type_ids,
                text,
                tokens,
                special_tokens_mask,
                offsets,
            ))

        # if it's train mode then extract the entities
        annotations = self.dataset['entities'][index]
        parsed_annotations, _ = self.parse_annotations(annotations, sort=True)
        token_level_tags = self.convert_to_token_level(tokens, offsets, special_tokens_mask, parsed_annotations)

        # convert token_level tags to it's corresponding id
        tags_ids = [self.tags2id[t] for t in token_level_tags]

        return self._truncate_output((
            t['input_ids'],
            token_type_ids,
            tags_ids,
            text,
            tokens,
            special_tokens_mask,
            offsets
        ))
    
    def _truncate_output(self, sample):
        input_ids, token_type_ids = sample[:2]
        input_ids = input_ids[:self.max_length] # input_ids
        token_type_ids = token_type_ids[:self.max_length] # token_type_ids
        
        sep_token = self.tokenizer.convert_tokens_to_ids("[SEP]")
        if input_ids[-1] != sep_token:
            input_ids[-1] = sep_token
        
        if not self.return_all:
            [() for e in sample[:2 + (self.mode != 'dev')]] # remove all other info

        if self.mode == 'dev':
            res = [input_ids, token_type_ids]
            if not self.return_all:
                return res
            res.extend(sample[2:])
            return res

        tags_ids = sample[2]
        tags_ids = tags_ids[:self.max_length]
        
        res = [input_ids, token_type_ids, tags_ids]
        if not self.return_all:
            return res
        
        res.extend(sample[3:])
        return res

    def _convert_to_iobes(self, tags):
        iobes_tags = []
        length = len(tags)

        for i, tag in enumerate(tags):
            if tag == 'O':
                iobes_tags.append(tag)
            else:
                if i == 0 or tags[i-1] != tag:
                    if i+1 == length or tags[i+1] != tag:
                        iobes_tags.append('S-' + tag)
                    else:
                        iobes_tags.append('B-' + tag)
                else:
                    if i+1 == length or tags[i+1] != tag:
                        iobes_tags.append('E-' + tag)
                    else:
                        iobes_tags.append('I-' + tag)

        return iobes_tags
    
    def convert_ids_to_tags(self, ids):
        return [self.id2tags.get(i, 'O') for i in ids]

    def _overlap(self, a, b):
        if a[0] <= b[0] <= a[1] or b[0] <= a[0] <= b[1]:
            return True
        return False
    

    # Function to parse the annotation data
    def parse_annotations(self, annotations, sort=True):
        parsed_annotations = []
        for annotation in annotations:
            start, end, entity_type = annotation.split()
            parsed_annotations.append((int(start), int(end), entity_type))

        parsed_annotations = sorted(parsed_annotations, key=lambda x: x[1] - x[0])

        filtered_annotations = []
        for annotation in parsed_annotations: # remove overlapping entities
            overlaps = sum([self._overlap(annotation, j) for j in filtered_annotations])
            if overlaps == 0:
                filtered_annotations.append(annotation)

        if sort:
            filtered_annotations = sorted(filtered_annotations, key=lambda x: x[0])    

        return filtered_annotations, parsed_annotations

    # Function to convert character offsets to token-level annotations
    def convert_to_token_level(self, tokens, offsets, special_tokens, annotation):
        token_level_annotations = ["O"] * len(tokens)

        # Iterate through annotations and align with tokenized text
        for start, end, entity_type in annotation:
            start_token_idx, end_token_idx = None, None
            for i, (special_token, (start_offset, end_offset)) in enumerate(zip(special_tokens, offsets)):
                if special_token == 1:
                    continue
                if start_offset == start:
                    start_token_idx = i
                if end_offset == end:
                    end_token_idx = i
                    break
            
            # If start_token_idx and end_token_idx are found, assign entity_type to corresponding tokens
            if start_token_idx is not None and end_token_idx is not None:
                for i in range(start_token_idx, end_token_idx + 1):
                    token_level_annotations[i] = entity_type
        
        assert len(tokens) == len(token_level_annotations)
        
        token_level_annotations_iobes = self._convert_to_iobes(token_level_annotations)
        assert len(token_level_annotations) == len(token_level_annotations_iobes)

        return token_level_annotations_iobes
    

def collate_batch(batch):
    max_length = max(len(x[0]) for x in batch)
    
    input_ids_list = []
    token_type_ids_list = []
    tags_ids_list = []
    for sample in batch:
        input_ids = sample[0]
        token_type_ids = sample[1]
        
        input_ids += [0] * (max_length - len(input_ids))
        token_type_ids += [0] * (max_length - len(token_type_ids))

        input_ids_list.append(torch.LongTensor(input_ids))
        token_type_ids_list.append(torch.LongTensor(token_type_ids))

        if len(sample) == 3:
            tags_ids = sample[2]
            tags_ids += [0] * (max_length - len(tags_ids))
            tags_ids_list.append(torch.LongTensor(tags_ids))
    
    output = [
        torch.stack(input_ids_list, dim=0),
        torch.stack(token_type_ids_list, dim=0),
    ]
    if len(tags_ids_list) > 0:
        output.append(torch.stack(tags_ids_list, dim=0))

    return output
    

if __name__ == '__main__':
    dataset = NERDataset(mode='train', max_length=256)
    sample = dataset[10]
    print('sample:', sample)
    print('length:', [len(i) for i in sample])

    from torch.utils.data import DataLoader
    dataloader = DataLoader(dataset, batch_size=8, collate_fn=collate_batch)
    for batch in dataloader:
        input_ids, token_type_ids, tags_ids = batch
        print('input_ids.shape:', input_ids.shape)
        print('token_type_ids.shape:', token_type_ids.shape)
        print('tags_ids.shape:', tags_ids.shape)
        break

    print('='*50)

    dataset = NERDataset(mode='dev')
    sample = dataset[10]
    print('sample:', sample)
    print('length:', [len(i) for i in sample])

    dataloader = DataLoader(dataset, batch_size=8, collate_fn=collate_batch)
    for batch in dataloader:
        input_ids, token_type_ids = batch
        print('input_ids.shape:', input_ids.shape)
        print('token_type_ids.shape:', token_type_ids.shape)
        break

    print('='*50)
    dataset = NERDataset(mode='train', max_length=32, return_all=True)
    sample = dataset[10]
    print('sample:', sample)
    print('tags:', dataset.convert_ids_to_tags(sample[2]))
    print('length:', [len(i) for i in sample])


    