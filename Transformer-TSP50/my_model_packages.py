import torch
from torch import nn
from torch.distributions.categorical import Categorical

class Transformer_encoder_net(nn.Module):
    """
    Encoder network based on self-attention transformer
    Inputs :  
      h of size      (bsz, nb_nodes+1, dim_emb)    batch of input cities
    Outputs :  
      h of size      (bsz, nb_nodes+1, dim_emb)    batch of encoded cities
      score of size  (bsz, nb_nodes+1, nb_nodes+1) batch of attention scores
    """
    def __init__(self, nb_layers, dim_emb, nb_heads, dim_ff, batchnorm):
        super(Transformer_encoder_net, self).__init__()
        assert dim_emb == nb_heads* (dim_emb//nb_heads) # check if dim_emb is divisible by nb_heads
        self.MHA_layers = nn.ModuleList( [nn.MultiheadAttention(dim_emb, nb_heads) for _ in range(nb_layers)] )
        self.linear1_layers = nn.ModuleList( [nn.Linear(dim_emb, dim_ff) for _ in range(nb_layers)] )
        self.linear2_layers = nn.ModuleList( [nn.Linear(dim_ff, dim_emb) for _ in range(nb_layers)] )   
        if batchnorm:
            self.norm1_layers = nn.ModuleList( [nn.BatchNorm1d(dim_emb) for _ in range(nb_layers)] )
            self.norm2_layers = nn.ModuleList( [nn.BatchNorm1d(dim_emb) for _ in range(nb_layers)] )
        else:
            self.norm1_layers = nn.ModuleList( [nn.LayerNorm(dim_emb) for _ in range(nb_layers)] )
            self.norm2_layers = nn.ModuleList( [nn.LayerNorm(dim_emb) for _ in range(nb_layers)] )
        self.nb_layers = nb_layers
        self.nb_heads = nb_heads
        self.batchnorm = batchnorm
        
    def forward(self, h):      
        # PyTorch nn.MultiheadAttention requires input size (seq_len, bsz, dim_emb) 
        h = h.transpose(0,1) # size(h)=(nb_nodes, bsz, dim_emb)  
        # L layers
        for i in range(self.nb_layers):
            h_rc = h # residual connection, size(h_rc)=(nb_nodes, bsz, dim_emb)
            h, score = self.MHA_layers[i](h, h, h) # size(h)=(nb_nodes, bsz, dim_emb), size(score)=(bsz, nb_nodes, nb_nodes)
            # add residual connection
            h = h_rc + h # size(h)=(nb_nodes, bsz, dim_emb)
            if self.batchnorm:
                # Pytorch nn.BatchNorm1d requires input size (bsz, dim, seq_len)
                h = h.permute(1,2,0).contiguous() # size(h)=(bsz, dim_emb, nb_nodes)
                h = self.norm1_layers[i](h)       # size(h)=(bsz, dim_emb, nb_nodes)
                h = h.permute(2,0,1).contiguous() # size(h)=(nb_nodes, bsz, dim_emb)
            else:
                h = self.norm1_layers[i](h)       # size(h)=(nb_nodes, bsz, dim_emb) 
            # feedforward
            h_rc = h # residual connection
            h = self.linear2_layers[i](torch.relu(self.linear1_layers[i](h)))
            h = h_rc + h # size(h)=(nb_nodes, bsz, dim_emb)
            if self.batchnorm:
                h = h.permute(1,2,0).contiguous() # size(h)=(bsz, dim_emb, nb_nodes)
                h = self.norm2_layers[i](h)       # size(h)=(bsz, dim_emb, nb_nodes)
                h = h.permute(2,0,1).contiguous() # size(h)=(nb_nodes, bsz, dim_emb)
            else:
                h = self.norm2_layers[i](h) # size(h)=(nb_nodes, bsz, dim_emb)
        # Transpose h
        h = h.transpose(0,1) # size(h)=(bsz, nb_nodes, dim_emb)
        return h, score
    

def myMHA(Q, K, V, nb_heads, mask=None, clip_value=None):
    """
    Compute multi-head attention (MHA) given a query Q, key K, value V and attention mask :
      h = Concat_{k=1}^nb_heads softmax(Q_k^T.K_k).V_k 
    Note : We did not use nn.MultiheadAttention to avoid re-computing all linear transformations at each call.
    Inputs : Q of size (bsz, dim_emb, 1)                batch of queries
             K of size (bsz, dim_emb, nb_nodes+1)       batch of keys
             V of size (bsz, dim_emb, nb_nodes+1)       batch of values
             mask of size (bsz, nb_nodes+1)             batch of masks of visited cities
             clip_value is a scalar 
    Outputs : attn_output of size (bsz, 1, dim_emb)     batch of attention vectors
              attn_weights of size (bsz, 1, nb_nodes+1) batch of attention weights
    """
    bsz, nb_nodes, emd_dim = K.size() #  dim_emb must be divisable by nb_heads
    if nb_heads>1:
        # PyTorch view requires contiguous dimensions for correct reshaping
        Q = Q.transpose(1,2).contiguous() # size(Q)=(bsz, dim_emb, 1)
        Q = Q.view(bsz*nb_heads, emd_dim//nb_heads, 1) # size(Q)=(bsz*nb_heads, dim_emb//nb_heads, 1)
        Q = Q.transpose(1,2).contiguous() # size(Q)=(bsz*nb_heads, 1, dim_emb//nb_heads)
        K = K.transpose(1,2).contiguous() # size(K)=(bsz, dim_emb, nb_nodes+1)
        K = K.view(bsz*nb_heads, emd_dim//nb_heads, nb_nodes) # size(K)=(bsz*nb_heads, dim_emb//nb_heads, nb_nodes+1)
        K = K.transpose(1,2).contiguous() # size(K)=(bsz*nb_heads, nb_nodes+1, dim_emb//nb_heads)
        V = V.transpose(1,2).contiguous() # size(V)=(bsz, dim_emb, nb_nodes+1)
        V = V.view(bsz*nb_heads, emd_dim//nb_heads, nb_nodes) # size(V)=(bsz*nb_heads, dim_emb//nb_heads, nb_nodes+1)
        V = V.transpose(1,2).contiguous() # size(V)=(bsz*nb_heads, nb_nodes+1, dim_emb//nb_heads)
    attn_weights = torch.bmm(Q, K.transpose(1,2))/ Q.size(-1)**0.5 # size(attn_weights)=(bsz*nb_heads, 1, nb_nodes+1)
    if clip_value is not None:
        attn_weights = clip_value * torch.tanh(attn_weights)
    if mask is not None:
        if nb_heads>1:
            mask = torch.repeat_interleave(mask, repeats=nb_heads, dim=0) # size(mask)=(bsz*nb_heads, nb_nodes+1)
        #attn_weights = attn_weights.masked_fill(mask.unsqueeze(1), float('-inf')) # size(attn_weights)=(bsz*nb_heads, 1, nb_nodes+1)
        attn_weights = attn_weights.masked_fill(mask.unsqueeze(1), float('-1e9')) # size(attn_weights)=(bsz*nb_heads, 1, nb_nodes+1)
    attn_weights = torch.softmax(attn_weights, dim=-1) # size(attn_weights)=(bsz*nb_heads, 1, nb_nodes+1)
    attn_output = torch.bmm(attn_weights, V) # size(attn_output)=(bsz*nb_heads, 1, dim_emb//nb_heads)
    if nb_heads>1:
        attn_output = attn_output.transpose(1,2).contiguous() # size(attn_output)=(bsz*nb_heads, dim_emb//nb_heads, 1)
        attn_output = attn_output.view(bsz, emd_dim, 1) # size(attn_output)=(bsz, dim_emb, 1)
        attn_output = attn_output.transpose(1,2).contiguous() # size(attn_output)=(bsz, 1, dim_emb)
        attn_weights = attn_weights.view(bsz, nb_heads, 1, nb_nodes) # size(attn_weights)=(bsz, nb_heads, 1, nb_nodes+1)
        attn_weights = attn_weights.mean(dim=1) # mean over the heads, size(attn_weights)=(bsz, 1, nb_nodes+1)
    return attn_output, attn_weights
    
    
class AutoRegressiveDecoderLayer(nn.Module):
    """
    Single decoder layer based on self-attention and query-attention
    Inputs :  
      h_t of size      (bsz, 1, dim_emb)          batch of input queries
      K_att of size    (bsz, nb_nodes+1, dim_emb) batch of query-attention keys
      V_att of size    (bsz, nb_nodes+1, dim_emb) batch of query-attention values
      mask of size     (bsz, nb_nodes+1)          batch of masks of visited cities
    Output :  
      h_t of size (bsz, nb_nodes+1)               batch of transformed queries
    """
    def __init__(self, dim_emb, nb_heads, segm_len):
        super(AutoRegressiveDecoderLayer, self).__init__()
        self.dim_emb = dim_emb
        self.nb_heads = nb_heads
        self.segm_len = segm_len
        self.Wq_selfatt = nn.Linear(dim_emb, dim_emb)
        self.Wk_selfatt = nn.Linear(dim_emb, dim_emb)
        self.Wv_selfatt = nn.Linear(dim_emb, dim_emb)
        self.W0_selfatt = nn.Linear(dim_emb, dim_emb)
        self.W0_att = nn.Linear(dim_emb, dim_emb)
        self.Wq_att = nn.Linear(dim_emb, dim_emb)
        self.W1_MLP = nn.Linear(dim_emb, dim_emb)
        self.W2_MLP = nn.Linear(dim_emb, dim_emb)
        self.BN_selfatt = nn.LayerNorm(dim_emb)
        self.BN_att = nn.LayerNorm(dim_emb)
        self.BN_MLP = nn.LayerNorm(dim_emb)
        self.K_sa = None
        self.V_sa = None

    def reset_selfatt_keys_values(self):
        self.K_sa = None
        self.V_sa = None
        
    # For beam search
    def reorder_selfatt_keys_values(self, t, idx_top_beams):
        if self.segm_len is not None:
            key_len = min(self.segm_len, t+1)
        else:
            key_len = t+1
            
        bsz, B = idx_top_beams.size()
        zero_to_B = torch.arange(B, device=idx_top_beams.device) # [0,1,...,B-1]
        B2 = self.K_sa.size(0)// bsz
        self.K_sa = self.K_sa.view(bsz, B2, key_len, self.dim_emb) # size(self.K_sa)=(bsz, B2, t+1, dim_emb)
        K_sa_tmp = self.K_sa.clone()
        self.K_sa = torch.zeros(bsz, B, key_len, self.dim_emb, device=idx_top_beams.device)
        for b in range(bsz):
            self.K_sa[b, zero_to_B, :, :] = K_sa_tmp[b, idx_top_beams[b], :, :]
        self.K_sa = self.K_sa.view(bsz*B, key_len, self.dim_emb) # size(self.K_sa)=(bsz*B, t+1, dim_emb)
        self.V_sa = self.V_sa.view(bsz, B2, key_len, self.dim_emb) # size(self.K_sa)=(bsz, B, t+1, dim_emb)
        V_sa_tmp = self.V_sa.clone()
        self.V_sa = torch.zeros(bsz, B, key_len, self.dim_emb, device=idx_top_beams.device)
        for b in range(bsz):
            self.V_sa[b, zero_to_B, :, :] = V_sa_tmp[b, idx_top_beams[b], :, :]
        self.V_sa = self.V_sa.view(bsz*B, key_len, self.dim_emb) # size(self.K_sa)=(bsz*B, t+1, dim_emb)

    # For beam search
    def repeat_selfatt_keys_values(self, B):
        self.K_sa = torch.repeat_interleave(self.K_sa, B, dim=0) # size(self.K_sa)=(bsz.B, t+1, dim_emb)
        self.V_sa = torch.repeat_interleave(self.V_sa, B, dim=0) # size(self.K_sa)=(bsz.B, t+1, dim_emb)
        
    def forward(self, h_t, K_att, V_att, mask):
        bsz = h_t.size(0)
        h_t = h_t.view(bsz,1,self.dim_emb) # size(h_t)=(bsz, 1, dim_emb)
        # embed the query for self-attention
        q_sa = self.Wq_selfatt(h_t) # size(q_sa)=(bsz, 1, dim_emb)
        k_sa = self.Wk_selfatt(h_t) # size(k_sa)=(bsz, 1, dim_emb)
        v_sa = self.Wv_selfatt(h_t) # size(v_sa)=(bsz, 1, dim_emb)
        # concatenate the new self-attention key and value to the previous keys and values
        if self.K_sa is None:
            self.K_sa = k_sa # size(self.K_sa)=(bsz, 1, dim_emb)
            self.V_sa = v_sa # size(self.V_sa)=(bsz, 1, dim_emb)
        else:
            self.K_sa = torch.cat([self.K_sa, k_sa], dim=1)
            self.V_sa = torch.cat([self.V_sa, v_sa], dim=1)
        if self.segm_len is not None:
            self.K_sa = self.K_sa[:, -self.segm_len:, :]
            self.V_sa = self.V_sa[:, -self.segm_len:, :]
        # compute self-attention between nodes in the partial tour
        h_t = h_t + self.W0_selfatt( myMHA(q_sa, self.K_sa, self.V_sa, self.nb_heads)[0] ) # size(h_t)=(bsz, 1, dim_emb)
        h_t = self.BN_selfatt(h_t.squeeze()) # size(h_t)=(bsz, dim_emb)
        h_t = h_t.view(bsz, 1, self.dim_emb) # size(h_t)=(bsz, 1, dim_emb)
        # compute attention between self-attention nodes and encoding nodes in the partial tour (translation process)
        q_a = self.Wq_att(h_t) # size(q_a)=(bsz, 1, dim_emb)
        h_t = h_t + self.W0_att( myMHA(q_a, K_att, V_att, self.nb_heads, mask)[0] ) # size(h_t)=(bsz, 1, dim_emb)
        h_t = self.BN_att(h_t.squeeze()) # size(h_t)=(bsz, dim_emb)
        h_t = h_t.view(bsz, 1, self.dim_emb) # size(h_t)=(bsz, 1, dim_emb)
        # MLP
        h_t = h_t + self.W2_MLP(torch.relu(self.W1_MLP(h_t)))
        h_t = self.BN_MLP(h_t.squeeze(1)) # size(h_t)=(bsz, dim_emb)
        return h_t
        
        
class Transformer_decoder_net(nn.Module): 
    """
    Decoder network based on self-attention and query-attention transformers
    Inputs :  
      h_t of size      (bsz, 1, dim_emb)                            batch of input queries
      K_att of size    (bsz, nb_nodes+1, dim_emb*nb_layers_decoder) batch of query-attention keys for all decoding layers
      V_att of size    (bsz, nb_nodes+1, dim_emb*nb_layers_decoder) batch of query-attention values for all decoding layers
      mask of size     (bsz, nb_nodes+1)                            batch of masks of visited cities
    Output :  
      prob_next_node of size (bsz, nb_nodes+1)                      batch of probabilities of next node
    """
    def __init__(self, dim_emb, nb_heads, nb_layers_decoder, segm_len):
        super(Transformer_decoder_net, self).__init__()
        self.dim_emb = dim_emb
        self.nb_heads = nb_heads
        self.nb_layers_decoder = nb_layers_decoder
        self.decoder_layers = nn.ModuleList( [AutoRegressiveDecoderLayer(dim_emb, nb_heads, segm_len) for _ in range(nb_layers_decoder-1)] )
        self.Wq_final = nn.Linear(dim_emb, dim_emb)
        
    # Reset to None self-attention keys and values when decoding starts 
    def reset_selfatt_keys_values(self): 
        for l in range(self.nb_layers_decoder-1):
            self.decoder_layers[l].reset_selfatt_keys_values()
            
    # For beam search
    def reorder_selfatt_keys_values(self, t, idx_top_beams):
        for l in range(self.nb_layers_decoder-1):
            self.decoder_layers[l].reorder_selfatt_keys_values(t, idx_top_beams)
    
    # For beam search
    def repeat_selfatt_keys_values(self, B):
        for l in range(self.nb_layers_decoder-1):
            self.decoder_layers[l].repeat_selfatt_keys_values(B)
     
    def forward(self, h_t, K_att, V_att, mask):
        for l in range(self.nb_layers_decoder):
            K_att_l = K_att[:,:,l*self.dim_emb:(l+1)*self.dim_emb].contiguous()  # size(K_att_l)=(bsz, nb_nodes+1, dim_emb)
            V_att_l = V_att[:,:,l*self.dim_emb:(l+1)*self.dim_emb].contiguous()  # size(V_att_l)=(bsz, nb_nodes+1, dim_emb)
            if l<self.nb_layers_decoder-1: # decoder layers with multiple heads (intermediate layers)
                h_t = self.decoder_layers[l](h_t, K_att_l, V_att_l, mask)
            else: # decoder layers with single head (final layer)
                q_final = self.Wq_final(h_t)
                bsz = h_t.size(0)
                q_final = q_final.view(bsz, 1, self.dim_emb)
                attn_weights = myMHA(q_final, K_att_l, V_att_l, 1, mask, 10)[1] 
        prob_next_node = attn_weights.squeeze(1) 
        return prob_next_node


def generate_positional_encoding(d_model, max_len):
    """
    Create standard transformer PEs.
    Inputs :  
      d_model is a scalar correspoding to the hidden dimension
      max_len is the maximum length of the sequence
    Output :  
      pe of size (max_len, d_model), where d_model=dim_emb, max_len=1000
    """
    pe = torch.zeros(max_len, d_model)
    position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
    div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-torch.log(torch.tensor(10000.0)) / d_model))
    pe[:,0::2] = torch.sin(position * div_term)
    pe[:,1::2] = torch.cos(position * div_term)
    return pe
    
    
class TSP_net(nn.Module): 
    """
    The TSP network is composed of two steps :
      Step 1. Encoder step : Take a set of 2D points representing a fully connected graph 
                             and encode the set with self-transformer.
      Step 2. Decoder step : Build the TSP tour recursively/autoregressively, 
                             i.e. one node at a time, with a self-transformer and query-transformer. 
    Inputs : 
      x of size (bsz, nb_nodes, dim_emb) Euclidian coordinates of the nodes/cities
      deterministic is a boolean : If True the salesman will chose the city with highest probability. 
                                   If False the salesman will chose the city with Bernouilli sampling.
    Outputs : 
      tours of size (bsz, nb_nodes) : batch of tours, i.e. sequences of ordered cities 
                                      tours[b,t] contains the idx of the city visited at step t in batch b
      sumLogProbOfActions of size (bsz,) : batch of sum_t log prob( pi_t | pi_(t-1),...,pi_0 )
    """
    
    def __init__(self, embedding, nb_neighbors, kernel_size, dim_input_nodes, dim_emb, dim_ff, nb_layers_encoder, nb_layers_decoder, nb_heads, max_len_PE,
                 segm_len=None, batchnorm=True):
        super(TSP_net, self).__init__()
        
        self.dim_emb = dim_emb
        
        # input embedding layer
        # self.input_emb = nn.Linear(dim_input_nodes, dim_emb)
        if embedding == 'linear':
            self.input_emb = nn.Linear(dim_input_nodes, dim_emb)
        elif embedding == 'conv':
            self.input_emb = ConvEmbedding(nb_neighbors, kernel_size, dim_emb, dim_input_nodes)
        elif embedding == 'conv_same_padding':
            self.input_emb = ConvSamePadding(dim_input_nodes, dim_emb, kernel_size)
        elif embedding == 'conv_linear':
            self.input_emb = ConvLinear(dim_input_nodes, dim_emb, kernel_size)
        elif embedding == 'convXY':
            self.input_emb = ConvEmbeddingXY(nb_neighbors, kernel_size, dim_emb, dim_input_nodes)
        
        # encoder layer
        self.encoder = Transformer_encoder_net(nb_layers_encoder, dim_emb, nb_heads, dim_ff, batchnorm)
        
        # vector to start decoding 
        self.start_placeholder = nn.Parameter(torch.randn(dim_emb))
        
        # decoder layer
        self.decoder = Transformer_decoder_net(dim_emb, nb_heads, nb_layers_decoder, segm_len)
        self.WK_att_decoder = nn.Linear(dim_emb, nb_layers_decoder* dim_emb) 
        self.WV_att_decoder = nn.Linear(dim_emb, nb_layers_decoder* dim_emb) 
        self.PE = generate_positional_encoding(dim_emb, max_len_PE)        
        
    def forward(self, x, B, greedy, beamsearch):
        
        # some parameters
        bsz = x.shape[0]
        nb_nodes = x.shape[1]
        zero_to_bsz = torch.arange(bsz, device=x.device) # [0,1,...,bsz-1]
        
        # For beam search
        zero_to_B = torch.arange(B, device=x.device) # [0,1,...,B-1]

        # input embedding layer
        h = self.input_emb(x) # size(h)=(bsz, nb_nodes, dim_emb)
        
        # concat the nodes and the input placeholder that starts the decoding
        h = torch.cat([h, self.start_placeholder.repeat(bsz, 1, 1)], dim=1) # size(start_placeholder)=(bsz, nb_nodes+1, dim_emb)
        
        # encoder layer
        h_encoder, _ = self.encoder(h) # size(h)=(bsz, nb_nodes+1, dim_emb)

        # key and value for decoder    
        K_att_decoder = self.WK_att_decoder(h_encoder) # size(K_att)=(bsz, nb_nodes+1, dim_emb*nb_layers_decoder)
        V_att_decoder = self.WV_att_decoder(h_encoder) # size(V_att)=(bsz, nb_nodes+1, dim_emb*nb_layers_decoder)
        
        # starting node in tour
        self.PE = self.PE.to(x.device)
        
        # For beam search
        tours_greedy = torch.zeros(2, nb_nodes, device=x.device)
        tours_beamsearch = torch.zeros(2, nb_nodes, device=x.device)
        scores_greedy = torch.zeros(2, device=x.device)
        scores_beamsearch = torch.zeros(2, device=x.device)
        
        # Greedy search
        if greedy:
            #print('Greedy decoding')
            deterministic = True
            # list that will contain Long tensors of shape (bsz,) that gives the idx of the cities chosen at time t
            tours = []
            # list that will contain Float tensors of shape (bsz,) that gives the neg log probs of the choices made at time t
            sumLogProbOfActions = []
            # input placeholder that starts the decoding
            idx_start_placeholder = torch.Tensor([nb_nodes]).long().repeat(bsz).to(x.device)
            h_start = h_encoder[zero_to_bsz, idx_start_placeholder, :] + self.PE[0].repeat(bsz,1) # size(h_start)=(bsz, dim_emb)
            # initialize mask of visited cities
            mask_visited_nodes = torch.zeros(bsz, nb_nodes+1, device=x.device).bool() # False
            mask_visited_nodes[zero_to_bsz, idx_start_placeholder] = True
            # clear key and val stored in the decoder
            self.decoder.reset_selfatt_keys_values()
            # construct tour recursively
            h_t = h_start
            for t in range(nb_nodes):
                # compute probability over the next node in the tour
                prob_next_node = self.decoder(h_t, K_att_decoder, V_att_decoder, mask_visited_nodes) # size(prob_next_node)=(bsz, nb_nodes+1)
                # choose node with highest probability or sample with Bernouilli 
                if deterministic:
                    idx = torch.argmax(prob_next_node, dim=1) # size(query)=(bsz,)
                else:
                    idx = Categorical(prob_next_node).sample() # size(query)=(bsz,)
                # compute logprobs of the action items in the list sumLogProbOfActions   
                ProbOfChoices = prob_next_node[zero_to_bsz, idx] 
                sumLogProbOfActions.append( torch.log(ProbOfChoices) )  # size(query)=(bsz,)
                # update embedding of the current visited node
                h_t = h_encoder[zero_to_bsz, idx, :] # size(h_start)=(bsz, dim_emb)
                h_t = h_t + self.PE[t+1].expand(bsz, self.dim_emb)
                # update tour
                tours.append(idx)
                # update masks with visited nodes
                mask_visited_nodes = mask_visited_nodes.clone()
                mask_visited_nodes[zero_to_bsz, idx] = True
            # logprob_of_choices = sum_t log prob( pi_t | pi_(t-1),...,pi_0 )
            sumLogProbOfActions = torch.stack(sumLogProbOfActions,dim=1).sum(dim=1) # size(sumLogProbOfActions)=(bsz,)
            # convert the list of nodes into a tensor of shape (bsz,num_cities)
            tours = torch.stack(tours,dim=1) # size(col_index)=(bsz, nb_nodes)
            tours_greedy = tours
            scores_greedy = sumLogProbOfActions 
        
        # Beamsearch
        if beamsearch:
            #print('Beam search decoding')
            # clear key and val stored in the decoder
            self.decoder.reset_selfatt_keys_values() 
            K_att_decoder_tmp = K_att_decoder # size(K_att_decoder_tmp)=(bsz, nb_nodes, dim_emb*nb_layers_decoder)
            V_att_decoder_tmp = V_att_decoder # size(V_att_decoder_tmp)=(bsz, nb_nodes, dim_emb*nb_layers_decoder)
            for t in range(nb_nodes):
                #if not t%10:
                #    print('t: {}, GPU reserved mem: {:.2f}, GPU allocated mem: {:.2f}'.format(t,torch.cuda.memory_reserved(0)/1e9,torch.cuda.memory_allocated(0)/1e9))
                if t==0: # at t=0, there are at most B_{t=0}=nb_nodes beams
                    B_t0 = min(B, nb_nodes)
                    # input placeholder that starts the decoding
                    idx_start_placeholder = torch.Tensor([nb_nodes]).long().repeat(bsz).to(x.device)
                    h_start = h_encoder[zero_to_bsz, idx_start_placeholder, :] + self.PE[0].repeat(bsz,1) # size(h_start)=(bsz, dim_emb)
                    h_t = h_start # size(h_start)=(bsz, dim_emb)
                    mask_visited_nodes = torch.zeros(bsz, nb_nodes+1, device=x.device).bool() # False, size(mask_visited_nodes)=(bsz, nb_nodes+1) # initialize mask of visited cities
                    mask_visited_nodes[zero_to_bsz, idx_start_placeholder] = True
                    # compute probability over the next node in the tour
                    prob_next_node = self.decoder(h_t, K_att_decoder, V_att_decoder, mask_visited_nodes) # size(prob_next_node)=(bsz, nb_nodes+1) 
                    # compute score_t + sum_t score_{t-1} for all beams
                    score_t = torch.log(prob_next_node) # size(score_t)=(bsz, nb_nodes+1) for t=0 
                    sum_scores = score_t # size(score_t)=(bsz, nb_nodes+1)
                    # choose nodes with top-B sumScores 
                    top_val, top_idx = torch.topk(sum_scores, B_t0, dim=1) # size(sumScores)=(bsz, B_t0)
                    # update sum_t score_{t} for all beams
                    sum_scores = top_val # size(sumScores)=(bsz, B_t0) 
                    zero_to_B_t0 = torch.arange(B_t0, device=x.device) # [0,1,...,B_t0-1]
                    mask_visited_nodes = mask_visited_nodes.unsqueeze(1) # size(mask_visited_nodes)=(bsz, 1, nb_nodes+1)
                    mask_visited_nodes = torch.repeat_interleave(mask_visited_nodes, B_t0, dim=1)
                    for b in range(bsz):
                        mask_visited_nodes[b, zero_to_B_t0, top_idx[b]] = True # size(mask_visited_nodes)=(bsz, B_t0, nb_nodes+1)
                    tours = torch.zeros(bsz, B_t0, nb_nodes, device=x.device).long() # size(tours)=(bsz, B_t0, nb_nodes)
                    tours[:,:,t] = top_idx # size(tours)=(bsz, B_t0, nb_nodes)
                    # update embedding of the current visited node
                    h_t = torch.zeros(bsz, B_t0, self.dim_emb, device=x.device) # size(tours)=(bsz, B_t0, dim_emb)
                    for b in range(bsz):
                        h_t[b, :, :] = h_encoder[b, top_idx[b], :] # size(h_t)=(bsz, B_t0, dim_emb)
                    h_t = h_t + self.PE[t+1].expand(bsz, B_t0, self.dim_emb) # size(h_t)=(bsz, B_t0, dim_emb)
                    self.decoder.repeat_selfatt_keys_values(B_t0)
                    K_att_decoder = torch.repeat_interleave(K_att_decoder_tmp, B_t0, dim=0) # size(K_att_decoder)=(bsz*B_t0, nb_nodes+1, dim_emb*nb_layers_decoder)
                    V_att_decoder = torch.repeat_interleave(V_att_decoder_tmp, B_t0, dim=0) # size(V_att_decoder)=(bsz*B_t0, nb_nodes+1, dim_emb*nb_layers_decoder)
                    
                elif t==1: # at t=1, there are at most B_{t=1}=nb_nodes^2 beams
                    # compute probability over the next node in the tour
                    h_t = h_t.view(bsz*B_t0, self.dim_emb)
                    mask_visited_nodes = mask_visited_nodes.view(bsz*B_t0, nb_nodes+1)
                    prob_next_node = self.decoder(h_t, K_att_decoder, V_att_decoder, mask_visited_nodes) # size(prob_next_node)=(bsz.B_t0, nb_nodes+1) 
                    prob_next_node = prob_next_node.view(bsz, B_t0, nb_nodes+1) # size(prob_next_node)=(bsz, B_t0, nb_nodes+1) 
                    mask_visited_nodes = mask_visited_nodes.view(bsz, B_t0, nb_nodes+1)
                    h_t = h_t.view(bsz, B_t0, self.dim_emb) 
                    # compute score_t + sum_t score_{t-1} for all beams
                    score_t = torch.log(prob_next_node) # size(score_t)=(bsz, B, nb_nodes+1) 
                    sum_scores = score_t + sum_scores.unsqueeze(2) # size(score_t)=(bsz, B, nb_nodes+1)
                    sum_scores_flatten = sum_scores.view(bsz, -1) # size(sumScores_next_node)=(bsz, B.(nb_nodes+1))
                    # choose nodes with top-B sumScores 
                    top_val, top_idx = torch.topk(sum_scores_flatten, B, dim=1)
                    idx_top_beams = top_idx // (nb_nodes+1) # size(idx_beam_topB)=(bsz, B)
                    idx_in_beams = top_idx - idx_top_beams* (nb_nodes+1) # size(idx_in_beams)=(bsz, B)
                    # update sum_t score_{t} for all beams
                    sum_scores = top_val
                    # update beam masks with visited nodes
                    mask_visited_nodes_tmp = mask_visited_nodes.clone() # size(mask_visited_nodes_tmp)=(bsz, B_t0, nb_nodes+1)
                    mask_visited_nodes = torch.zeros(bsz, B, nb_nodes+1, device=x.device).bool() # size(mask_visited_nodes)=(bsz, B, nb_nodes+1)
                    for b in range(bsz):
                        mask_visited_nodes[b, zero_to_B, :] = mask_visited_nodes_tmp[b, idx_top_beams[b], :] # size(mask_visited_nodes)=(bsz, B, nb_nodes+1)
                    for b in range(bsz):
                        mask_visited_nodes[b, zero_to_B, idx_in_beams[b]] = True # size(mask_visited_nodes)=(bsz, B, nb_nodes+1)
                    # update beam tours with visited nodes
                    tours_tmp = tours.clone()
                    tours = torch.zeros(bsz, B, nb_nodes, device=x.device).long() # size(tours)=(bsz, B, nb_nodes)
                    for b in range(bsz):
                        tours[b, zero_to_B, :] = tours_tmp[b, idx_top_beams[b], :]
                    tours[:,:,t] = idx_in_beams # size(tours)=(bsz, B, nb_nodes)
                    # update embedding of the current visited node
                    h_t = torch.zeros(bsz, B, self.dim_emb, device=x.device) # size(tours)=(bsz, B_t0, dim_emb)
                    for b in range(bsz):
                        h_t[b, :, :] = h_encoder[b, idx_in_beams[b], :] # size(h_t)=(bsz, B, dim_emb)
                    h_t = h_t + self.PE[t+1].expand(bsz, B, self.dim_emb) # size(h_t)=(bsz, B, dim_emb)
                    # update self-attention embeddings of partial tours
                    self.decoder.reorder_selfatt_keys_values(t, idx_top_beams) # size(K_att_decoder)=(bsz*B_t0, nb_nodes+1, dim_emb*nb_layers_decoder)
                    K_att_decoder = torch.repeat_interleave(K_att_decoder_tmp, B, dim=0) # size(K_att_decoder)=(bsz*B, nb_nodes+1, dim_emb*nb_layers_decoder)
                    V_att_decoder = torch.repeat_interleave(V_att_decoder_tmp, B, dim=0) # size(V_att_decoder)=(bsz*B, nb_nodes+1, dim_emb*nb_layers_decoder)

                else: # at t>=2, we arbitrary decide to have at most B_{t>=2}=nb_nodes^2 beams
                    # compute probability over the next node in the tour
                    h_t = h_t.view(bsz*B, self.dim_emb)
                    mask_visited_nodes = mask_visited_nodes.view(bsz*B, nb_nodes+1)
                    prob_next_node = self.decoder(h_t, K_att_decoder, V_att_decoder, mask_visited_nodes) # size(prob_next_node)=(bsz.B, nb_nodes+1) 
                    prob_next_node = prob_next_node.view(bsz, B, nb_nodes+1) # size(prob_next_node)=(bsz, B, nb_nodes+1) 
                    mask_visited_nodes = mask_visited_nodes.view(bsz, B, nb_nodes+1)
                    h_t = h_t.view(bsz, B, self.dim_emb) 
                    # compute score_t + sum_t score_{t-1} for all beams
                    score_t = torch.log(prob_next_node) # size(score_t)=(bsz, B, nb_nodes+1)
                    sum_scores = score_t + sum_scores.unsqueeze(2) # size(score_t)=(bsz, B, nb_nodes+1)
                    sum_scores_flatten = sum_scores.view(bsz, -1) # size(sumScores_next_node)=(bsz, B.(nb_nodes+1))
                    # choose nodes with top-B sumScores 
                    top_val, top_idx = torch.topk(sum_scores_flatten, B, dim=1)
                    idx_top_beams = top_idx // (nb_nodes+1) # size(idx_beam_topB)=(bsz, B)
                    idx_in_beams = top_idx - idx_top_beams* (nb_nodes+1) # size(idx_in_beams)=(bsz, B)
                    # update sum_t score_{t} for all beams
                    sum_scores = top_val
                    # update beam masks with visited nodes
                    mask_visited_nodes_tmp = mask_visited_nodes.clone()
                    for b in range(bsz):
                        mask_visited_nodes[b, zero_to_B, :] = mask_visited_nodes_tmp[b, idx_top_beams[b], :]
                    for b in range(bsz):
                        mask_visited_nodes[b, zero_to_B, idx_in_beams[b]] = True
                    # update beam tours with visited nodes
                    tours_tmp = tours.clone()
                    for b in range(bsz):
                        tours[b, zero_to_B, :] = tours_tmp[b, idx_top_beams[b], :]
                    tours[:,:,t] = idx_in_beams # size(tours)=(bsz, B, nb_nodes)
                    # update embedding of the current visited node
                    for b in range(bsz):
                        h_t[b, :, :] = h_encoder[b, idx_in_beams[b], :] # size(h_t)=(bsz, B, dim_emb)
                    h_t = h_t + self.PE[t+1].expand(bsz, B, self.dim_emb) # size(h_t)=(bsz, B, dim_emb)
                    # update self-attention embeddings of partial tours
                    self.decoder.reorder_selfatt_keys_values(t, idx_top_beams)
            # sum_t log prob( pi_t | pi_0,...pi_(t-1) )
            sum_scores = sum_scores[:,0] # size(sumScores)=(bsz)
            tours_beamsearch = tours
            scores_beamsearch = sum_scores

        return tours_greedy, tours_beamsearch, scores_greedy, scores_beamsearch

class ConvEmbedding(nn.Module):
    def __init__(self, nb_neighbors, kernel_size, dim_emb, dim_input_nodes):
        super().__init__()
        self.nb_neighbors = nb_neighbors
        self.conv = nn.Conv1d(in_channels=dim_input_nodes, out_channels=dim_emb, kernel_size=kernel_size)
        self.W1 = nn.Linear(dim_input_nodes, dim_emb)  # for node x_i
        self.W2 = nn.Linear(dim_emb, dim_emb)  # for convolved node feature hbar_i

    def forward(self, x):
        """
        :param Tensor x: (B, N, 2)
        :return Tensor final_embedding: (B, N, H)
        """
        bsz, seq_len = x.size(0), x.size(1)

        node_embedding = self.W1(x)  # (B, N, H)

        # Make k-NN for each node (B, N, K+1, 2)
        dist_matrix = torch.cdist(x, x)  # (B, N, N)
        # knn_indices = dist_matrix.topk(self.nb_neighbors+1)[1]  # (B, N, K+1) including itself
        knn_indices = dist_matrix.topk(k=seq_len)[1][:,:,-self.nb_neighbors-1:]  # (B, N, K+1) including itself
        embedding_list = []
        for i in range(seq_len):
            idx = knn_indices[:, i, :].unsqueeze(2).repeat(1,1,2)  # (B, K+1, 2)
            knn_coords = x.gather(1, idx)

            knn_coords = knn_coords.permute(0, 2, 1)  # (B, 2, K+1)
            conv_embedding = self.conv(knn_coords)  # (B, H, 1)
            
            conv_embedding = conv_embedding.permute(0, 2, 1)  # (B, 1, H)
            # conv_embedding = self.W2(conv_embedding)  # (B, 1, H)  comment

            embedding_list.append(conv_embedding)
        conv_embedding = torch.cat(embedding_list, dim=1)  # (B, N, H)
        conv_embedding = self.W2(conv_embedding)  # (B, N, H)  namely CEFix

        final_embedding = node_embedding + conv_embedding  # (B, N, H)
        return final_embedding

class ConvSamePadding(nn.Module):
    def __init__(self, dim_input_nodes, dim_emb, kernel_size):
        super().__init__()
        self.conv = nn.Conv1d(in_channels=dim_input_nodes, out_channels=dim_emb, kernel_size=kernel_size, padding='same')
    def forward(self, x):
        """(B, N, 2) --> (B, N, H)"""
        x = x.permute(0,2,1)  # (B, 2, N)
        h = self.conv(x)  # (B, H, N)
        h = h.permute(0, 2, 1)  # (B, N, H)
        return h

class ConvLinear(nn.Module):
    def __init__(self, dim_input_nodes, dim_emb, kernel_size):
        super().__init__()
        self.conv = nn.Conv1d(in_channels=dim_input_nodes, out_channels=dim_emb, kernel_size=kernel_size, padding='same')
        self.W1 = nn.Linear(dim_input_nodes, dim_emb)
        self.W2 = nn.Linear(dim_emb, dim_emb)
    def forward(self, x):
        """(B, N, 2) --> (B, N, H)"""
        node_embedding = self.W1(x)  # (B, N, H)
        x = x.permute(0, 2, 1)  # (B, 2, N)
        conv_embedding = self.conv(x)  # (B, H, N)
        conv_embedding = conv_embedding.permute(0, 2, 1)  # (B, N, H)
        final_embedding = node_embedding + conv_embedding
        return final_embedding

class ConvEmbeddingXY(nn.Module):
    def __init__(self, nb_neighbors, kernel_size, dim_emb, dim_input_nodes):
        super().__init__()
        self.nb_neighbors = nb_neighbors
        self.conv_x = nn.Conv1d(in_channels=dim_input_nodes, out_channels=dim_emb, kernel_size=kernel_size)
        self.conv_y = nn.Conv1d(in_channels=dim_input_nodes, out_channels=dim_emb, kernel_size=kernel_size)
        self.W1 = nn.Linear(dim_input_nodes, dim_emb)  # for node x_i
        self.W2 = nn.Linear(dim_emb, dim_emb)  # for convolved node feature hbar_i
    
    def _sort_by_xy(self, coord):
        """
        :param Tensor coord: (B, K, 2)
        """
        toB = torch.arange(coord.size(0))
        indices = coord[:, :, 0].argsort()  # sort by x coordinate (B, K)
        coord = coord[toB.unsqueeze(1), indices]  # (B, K, 2)
        return coord
    
    def _sort_by_yx(self, coord):
        toB = torch.arange(coord.size(0))
        indices = coord[:, :, 1].argsort()  # sort by y coordinate
        coord = coord[toB.unsqueeze(1), indices]
        return coord

    def forward(self, x):
        """
        :param Tensor x: (B, N, 2)
        :return Tensor final_embedding: (B, N, H)
        """
        bsz, seq_len = x.size(0), x.size(1)

        node_embedding = self.W1(x)  # (B, N, H)

        # Make k-NN for each node (B, N, K+1, 2)
        dist_matrix = torch.cdist(x, x)  # (B, N, N)
        knn_indices = dist_matrix.topk(k=seq_len)[1][:,:,-self.nb_neighbors-1:]  # (B, N, K+1) including itself

        embedding_list = []
        for i in range(seq_len):
            idx = knn_indices[:, i, :].unsqueeze(2).repeat(1,1,2)  # (B, K+1, 2)
            knn_coords = x.gather(1, idx)

            knn_coords_x = self._sort_by_xy(knn_coords)  # (B, K+1, 2)
            knn_coords_y = self._sort_by_yx(knn_coords)  # (B, K+1, 2)

            knn_coords_x = knn_coords_x.permute(0, 2, 1)  # (B, 2, K+1)
            conv_embedding_x = self.conv_x(knn_coords_x)  # (B, H, 1)
            
            knn_coords_y = knn_coords_y.permute(0, 2, 1)  # (B, 2, K+1)
            conv_embedding_y = self.conv_y(knn_coords_y)  # (B, H, 1)

            conv_embedding = conv_embedding_x + conv_embedding_y  # (B, H, 1)

            conv_embedding = conv_embedding.permute(0, 2, 1)  # (B, 1, H)
            # conv_embedding = self.W2(conv_embedding)  # (B, 1, H)

            embedding_list.append(conv_embedding)
        conv_embedding = torch.cat(embedding_list, dim=1)  # (B, N, H)
        conv_embedding = self.W2(conv_embedding)  # (B, N, H)  namely CEFix

        final_embedding = node_embedding + conv_embedding  # (B, N, H)
        return final_embedding
     
def compute_tour_length(x, tour): 
    """
    Compute the length of a batch of tours
    Inputs : x of size (bsz, nb_nodes, 2) batch of tsp tour instances
             tour of size (bsz, nb_nodes) batch of sequences (node indices) of tsp tours
    Output : L of size (bsz,)             batch of lengths of each tsp tour
    """
    bsz = x.shape[0]
    nb_nodes = x.shape[1]
    arange_vec = torch.arange(bsz, device=x.device)
    first_cities = x[arange_vec, tour[:,0], :] # size(first_cities)=(bsz,2)
    previous_cities = first_cities
    L = torch.zeros(bsz, device=x.device)
    with torch.no_grad():
        for i in range(1,nb_nodes):
            current_cities = x[arange_vec, tour[:,i], :] 
            L += (current_cities - previous_cities).pow(2).sum(dim=1).sqrt()
            # L += torch.sum( torch.round((current_cities - previous_cities)**2) , dim=1 )**0.5 # dist(current, previous node) 
            previous_cities = current_cities
        L += (current_cities - first_cities).pow(2).sum(dim=1).sqrt()
        # L += torch.sum( torch.round((current_cities - first_cities)**2) , dim=1 )**0.5 # dist(last, first node)  
    return L